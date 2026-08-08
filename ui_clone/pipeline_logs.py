"""Bounded logging helpers for ui-clone pipeline subprocesses.

Long browser/capture/gate commands can produce enough stdout/stderr to bloat
agent transcripts and make Codex resume sessions fragile. These helpers keep the
full evidence on disk while printing only concise summaries/tails.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

_DEFAULT_TAIL_LINES = 120
_FALSEY = {"", "0", "false", "no", "off"}


def log_tail_lines(env: Mapping[str, str] | None = None) -> int:
    """Return max log lines to echo for failures.

    `UI_CLONE_LOG_TAIL_LINES=0` suppresses tails entirely. Invalid values fall
    back to the conservative default instead of failing pipeline work.
    """

    source = os.environ if env is None else env
    raw = source.get("UI_CLONE_LOG_TAIL_LINES", str(_DEFAULT_TAIL_LINES)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_TAIL_LINES


def echo_success_output(env: Mapping[str, str] | None = None) -> bool:
    """Whether successful step output should be echoed as a bounded tail."""

    source = os.environ if env is None else env
    return source.get("UI_CLONE_ECHO_SUCCESS_OUTPUT", "").strip().lower() not in _FALSEY


def label_slug(label: str) -> str:
    """Make a stable filesystem-safe log name from a phase/gate label."""

    parts: list[str] = []
    previous_dash = False
    for char in label.lower():
        if char.isalnum():
            parts.append(char)
            previous_dash = False
        elif not previous_dash:
            parts.append("-")
            previous_dash = True
    slug = "".join(parts).strip("-")
    return slug or "step"


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def completed_process_output(
    result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
) -> str:
    """Return stdout plus stderr with a separator when both are present."""

    stdout = _as_text(result.stdout)
    stderr = _as_text(result.stderr)
    if stdout and stderr:
        return stdout.rstrip() + "\n--- stderr ---\n" + stderr.rstrip() + "\n"
    return stdout or stderr


def timeout_output(exc: subprocess.TimeoutExpired) -> str:
    stdout = _as_text(exc.stdout)
    stderr = _as_text(exc.stderr)
    parts = [f"timeout after {exc.timeout}s"]
    if stdout.strip():
        parts.append("--- stdout before timeout ---")
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append("--- stderr before timeout ---")
        parts.append(stderr.rstrip())
    return "\n".join(parts) + "\n"


def write_process_log(
    ref_dir: Path,
    scope: str,
    label: str,
    output: str,
    *,
    command: Sequence[str] | None = None,
    exit_code: int | str | None = None,
) -> Path:
    """Write a subprocess log under tmp/ref/<component>/logs/<scope>/.

    The log always includes command/exit metadata when provided, even when the
    command itself produced no output.
    """

    log_dir = ref_dir / "logs" / scope
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{label_slug(label)}.log"
    header: list[str] = []
    if command:
        header.append("$ " + " ".join(command))
    if exit_code is not None:
        header.append(f"exit: {exit_code}")
    body = "\n".join(header)
    if body and output:
        body += "\n\n"
    body += output
    if body and not body.endswith("\n"):
        body += "\n"
    log_path.write_text(body, encoding="utf-8")
    return log_path


def tail_text(text: str, max_lines: int) -> str:
    """Return the last max_lines lines of text, or empty when disabled."""

    if max_lines <= 0:
        return ""
    lines = text.rstrip().splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])
