"""ui_clone.shell — bash 4+ resolution for Python-side script dispatch.

Regression for the loop-153 Phase 2.5 failure: pipeline dispatch via bare
``["bash", script]`` resolved macOS /bin/bash 3.2, which cannot parse a
heredoc nested inside ``$(...)`` (extract-asset-metadata.sh line 30), so
the script died with "unexpected EOF while looking for matching `''" on a
machine where the same script parses clean under bash 4+.
"""

from __future__ import annotations

import os
import subprocess

from ui_clone.shell import bash_bin, bash_env


def _major(binary: str) -> int:
    out = subprocess.run(
        [binary, "-c", 'printf %s "${BASH_VERSION%%.*}"'],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    ).stdout.strip()
    return int(out or "0")


def _any_bash4_on_host() -> bool:
    candidates = ["bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash"]
    for cand in candidates:
        try:
            if _major(cand) >= 4:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def test_bash_bin_prefers_bash4_when_available() -> None:
    resolved = bash_bin()
    assert os.path.isabs(resolved) or resolved == "bash"
    if _any_bash4_on_host():
        assert _major(resolved) >= 4, (
            f"bash_bin() resolved {resolved} (major {_major(resolved)}) "
            "even though a bash 4+ binary exists on this host"
        )


def test_bash_bin_parses_heredoc_inside_command_substitution() -> None:
    # The exact 3.2-fatal construct from extract-asset-metadata.sh.
    snippet = "JS=$(cat <<'JS'\nvalue with 'quotes' and \"doubles\"\nJS\n); printf %s \"$JS\""
    if not _any_bash4_on_host():
        return  # nothing to assert on a bash-3-only host
    proc = subprocess.run(
        [bash_bin(), "-c", snippet],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "value with 'quotes' and \"doubles\""


def test_bash_env_prepends_resolved_bin_dir() -> None:
    env = bash_env({"PATH": "/bin:/usr/bin"})
    first = env["PATH"].split(os.pathsep)[0]
    assert first == os.path.dirname(bash_bin())


def test_bash_env_does_not_mutate_base() -> None:
    base = {"PATH": "/bin"}
    bash_env(base)
    assert base == {"PATH": "/bin"}
