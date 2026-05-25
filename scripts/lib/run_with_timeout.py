#!/usr/bin/env python3
"""Cross-platform process-group-aware timeout wrapper.

Why this exists:
  scripts/lib/timeout-shim.sh's pure-bash fallback (`"$@" &` background +
  watchdog sleep + `kill -TERM`) interacts badly with sub-check process
  groups when invoked inside a pipefail-enabled `if cmd | tail | sed; then`
  pipeline (revealed by run-required-checks test regressions during the
  2026-05-25 timeout-injection attempt). The fallback's TERM only hits the
  immediate child PID, leaving the child's spawned tree (bash → node →
  chromium → …) alive and the wait blocked.

  Python's `subprocess.Popen(start_new_session=True)` + `os.killpg()` is
  the portable fix: child is placed in its own process group, timeout
  fires a process-group SIGTERM (so the whole tree gets the signal), and
  SIGKILL escalates after a 5s grace period.

Usage:
  python3 scripts/lib/run_with_timeout.py <seconds> <cmd> [args...]

Exit codes (matching GNU timeout):
  - <child exit code>  — normal completion under the deadline
  - 124                — timed out, group killed
  - 125                — wrapper / argument error
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys


def _parse_seconds(arg: str) -> float:
    """Accept bare numbers or GNU suffix (s/m/h). Raises ValueError on junk."""
    if not arg:
        raise ValueError("empty timeout")
    suffix = arg[-1]
    if suffix == "s":
        return float(arg[:-1])
    if suffix == "m":
        return float(arg[:-1]) * 60
    if suffix == "h":
        return float(arg[:-1]) * 3600
    return float(arg)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "usage: run_with_timeout.py <seconds> <cmd> [args...]",
            file=sys.stderr,
        )
        return 125
    try:
        seconds = _parse_seconds(argv[1])
        if seconds <= 0:
            raise ValueError(f"timeout must be > 0, got {seconds}")
    except ValueError as exc:
        print(f"run_with_timeout: invalid timeout {argv[1]!r}: {exc}", file=sys.stderr)
        return 125

    cmd = argv[2:]
    # start_new_session=True → child becomes its own session/process-group
    # leader. killpg(pgid, SIG) then reaches every descendant the child
    # spawned, not just the immediate PID.
    try:
        proc = subprocess.Popen(cmd, start_new_session=True)
    except (OSError, FileNotFoundError) as exc:
        print(f"run_with_timeout: cannot exec {cmd[0]!r}: {exc}", file=sys.stderr)
        return 125

    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            # Child already gone between wait() and getpgid() — race-safe
            return proc.wait()
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return proc.wait()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        print(
            f"run_with_timeout: command exceeded {seconds}s and was "
            f"process-group-killed",
            file=sys.stderr,
        )
        return 124
    except KeyboardInterrupt:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGINT)
        except ProcessLookupError:
            pass
        proc.wait()
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv))
