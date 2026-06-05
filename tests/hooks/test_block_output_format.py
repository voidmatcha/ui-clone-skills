"""The block payload must enforce on BOTH hosts.

codex-cli 0.137 honors a top-level ``{"decision": "block", "reason": ...}``
(exit 0); Claude Code honors the nested
``hookSpecificOutput.permissionDecision: "deny"``. The gate hooks must dual-emit
so a deny is enforced on either host — emitting only the Claude shape let codex
run the command anyway (confirmed by a live smoke-test).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from ui_clone.hooks.pre_bash_rules.dispatcher import _emit_block as _pre_bash_emit_block
from ui_clone.hooks.pre_generate import _emit_block as _pre_generate_emit_block


@pytest.mark.parametrize(
    "emit",
    [_pre_bash_emit_block, _pre_generate_emit_block],
    ids=["pre_bash", "pre_generate"],
)
def test_emit_block_dual_emits_codex_and_claude_deny(
    emit: Callable[[str], None], capsys: pytest.CaptureFixture[str]
) -> None:
    reason = "⛔ run the pipeline driver FIRST"
    emit(reason)
    out = json.loads(capsys.readouterr().out)
    # Codex 0.137 block protocol (top-level), exit 0
    assert out["decision"] == "block"
    assert out["reason"] == reason
    # Claude Code block protocol (nested)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == reason
