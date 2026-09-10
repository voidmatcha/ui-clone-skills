"""Push-verdict detection in scripts/hooks/post-push-refresh.sh.

Extracts the embedded python verdict snippet directly (rather than running the
full bash script, which always tails into a real `scripts/ci/review.sh` run
against this repo regardless of UI_CLONE_SKIP_POST_PUSH_REFRESH) so these
tests stay fast and target exactly the logic that was fixed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


SCRIPT = _project_root() / "scripts" / "hooks" / "post-push-refresh.sh"


def _extract_verdict_snippet() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"_verdict=\$\(HOOK_INPUT=\"\$input\" python3 -c '\n(.*?)\n'", text, re.S)
    assert match, "verdict python snippet not found — update this test's regex"
    return match.group(1)


def _verdict(payload: dict) -> str:
    snippet = _extract_verdict_snippet()
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"HOOK_INPUT": json.dumps(payload)},
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_multiline_command_with_push_on_a_later_line_is_still_detected() -> None:
    # fable-20260910 follow-up (Codex dev-hook parity review, finding 3): an
    # earlier version printed the raw (possibly multi-line) command on its
    # own line and let bash `sed -n '1p'/'2p'` split it — a command like
    # "git commit ...\ngit push ..." had its push hidden past line 1 and was
    # silently treated as a non-push. The verdict is now a single word
    # computed entirely in python, so this can no longer happen.
    payload = {
        "tool_input": {"command": "git commit -q -m x\ngit push origin main"},
        "exit_code": 0,
    }
    assert _verdict(payload) == "push-ok"


def test_non_push_command_is_not_push() -> None:
    assert _verdict({"tool_input": {"command": "ls -la"}, "exit_code": 0}) == "not-push"


def test_push_with_nonzero_exit_is_unconfirmed() -> None:
    payload = {"tool_input": {"command": "git push origin main"}, "exit_code": 1}
    assert _verdict(payload) == "push-unconfirmed"


def test_push_with_missing_exit_code_is_unconfirmed() -> None:
    # Fail-closed: an unrecognized/absent exit_code field must NOT default to
    # "assume success" (this file's own header: "Triggers only when the tool
    # input was a successful git push").
    payload = {"tool_input": {"command": "git push origin main"}}
    assert _verdict(payload) == "push-unconfirmed"


def test_push_with_nested_tool_response_exit_code() -> None:
    payload = {
        "tool_input": {"command": "git push origin main"},
        "tool_response": {"exit_code": 0},
    }
    assert _verdict(payload) == "push-ok"
