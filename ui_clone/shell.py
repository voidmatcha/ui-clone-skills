"""Shared bash 4+ resolution for repo script dispatch.

macOS ships bash 3.2 as /bin/bash, and 3.2 cannot parse a heredoc nested
inside `$(...)` command substitution — a construct repo scripts legitimately
use (project policy is bash 4+ minimum; see the "Resolve a bash 4+ binary"
block in scripts/ci/ci-local.sh). Python-side dispatch of `["bash", ...]`
resolves through PATH where /bin/bash usually wins, so every subprocess
dispatch of a repo bash script must go through bash_bin() — and pass
bash_env() when the script itself re-invokes `bash` or `#!/usr/bin/env bash`
children, so the pinned interpreter wins for the whole process tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from functools import cache

_FALLBACK_CANDIDATES = ("/opt/homebrew/bin/bash", "/usr/local/bin/bash")


def _bash_major(binary: str) -> int:
    try:
        proc = subprocess.run(
            [binary, "-c", 'printf %s "${BASH_VERSION%%.*}"'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return 0


@cache
def bash_bin() -> str:
    """Absolute path of a bash 4+ binary, falling back to PATH bash."""
    default = shutil.which("bash") or "/bin/bash"
    if _bash_major(default) >= 4:
        return default
    for cand in _FALLBACK_CANDIDATES:
        if os.access(cand, os.X_OK) and _bash_major(cand) >= 4:
            return cand
    return default


def bash_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of *base* (default os.environ) with bash_bin()'s dir first on PATH.

    Always prepend (not "add if absent") so bash 4+ wins even when its dir is
    already on PATH but sits behind /bin (where macOS's 3.2 lives).
    """
    env = dict(os.environ if base is None else base)
    bin_dir = os.path.dirname(bash_bin())
    path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{os.pathsep}{path}" if path else bin_dir
    return env
