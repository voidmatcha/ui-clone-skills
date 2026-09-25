"""The Stop hook must retry a failing gate a few times, then say so out loud.

Before this, the re-entrancy guard exited 0 the first time Claude Code reported
``stop_hook_active``, delegating closeout to "the driver's STATUS marker + stall
watchdog" -- machinery that only exists in the benchmark harness. In an ordinary
interactive session that meant exactly one nudge and then a silent stop with
gates still failing. Observed in the field as a ten-hour stall whose only
evidence was a file mtime: the section-compare gate had recorded critical
failures and nothing ever acted on them.

Two properties are pinned here:
  * the budget is spent across several stops, not one, and
  * when it IS spent the run does not end in silence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui_clone.hooks import section_gate


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "tmp" / "ref").mkdir(parents=True)
    return tmp_path


def _attempts(project: Path) -> dict[str, int]:
    return section_gate._read_stop_attempts(project)


def test_ledger_roundtrips(project: Path) -> None:
    section_gate._write_stop_attempts(project, {"sid": 2})
    assert _attempts(project) == {"sid": 2}


def test_corrupt_ledger_reads_as_empty(project: Path) -> None:
    """A damaged counter must degrade to "no attempts yet", never crash a hook."""
    section_gate._stop_attempts_path(project).write_text("{not json", encoding="utf-8")
    assert _attempts(project) == {}


def test_non_int_entries_are_dropped(project: Path) -> None:
    section_gate._stop_attempts_path(project).write_text(
        json.dumps({"good": 1, "bad": "2", "worse": None}), encoding="utf-8"
    )
    assert _attempts(project) == {"good": 1}


def test_unwritable_ledger_does_not_raise(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "write_text", boom)
    section_gate._write_stop_attempts(project, {"sid": 1})


def test_cap_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UI_RE_STOP_RETRY_CAP", raising=False)
    assert section_gate._stop_retry_cap() == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", 5), ("0", 0), ("-2", 0), ("", 3), ("abc", 3)],
)
def test_cap_env_override(
    raw: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UI_RE_STOP_RETRY_CAP", raw)
    assert section_gate._stop_retry_cap() == expected


def test_budget_spans_several_stops_then_is_spent(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: one nudge used to be the entire budget."""
    monkeypatch.delenv("UI_RE_STOP_RETRY_CAP", raising=False)
    cap = section_gate._stop_retry_cap()
    assert cap >= 2, "a cap of 1 would reintroduce the single-nudge stall"

    sid = "session-abc"
    for expected in range(1, cap + 1):
        attempts = _attempts(project)
        used = attempts.get(sid, 0)
        assert used < cap, "budget exhausted earlier than the cap"
        attempts[sid] = used + 1
        section_gate._write_stop_attempts(project, attempts)
        assert _attempts(project)[sid] == expected

    assert _attempts(project).get(sid, 0) >= cap


def test_advisory_mode_prints_instead_of_blocking(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the budget spent the stop is allowed -- but never in silence.

    Stderr on exit 0 is debug-log only under the Stop-hook contract, so the
    hand-back must travel as a systemMessage, which the host shows to the user.
    """
    monkeypatch.delenv("UI_RE_HEADLESS_DRIVER", raising=False)
    monkeypatch.setattr(section_gate, "_ADVISORY_ONLY", True)
    section_gate._emit_block("GATE: section-compare BLOCKED\n  - hero mismatch")
    captured = capsys.readouterr()

    shown = json.loads(captured.out)
    assert "decision" not in shown, "advisory mode must not emit a block decision"
    message = shown["systemMessage"]
    assert "UNFINISHED" in message
    assert "INCOMPLETE" in message
    assert "hero mismatch" in message, "the user must be told WHICH gate failed"


def test_normal_mode_still_blocks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("UI_RE_HEADLESS_DRIVER", raising=False)
    monkeypatch.setattr(section_gate, "_ADVISORY_ONLY", False)
    section_gate._emit_block("still failing")
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "decision": "block",
        "reason": "still failing",
    }


def test_headless_driver_is_unaffected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The driver already re-runs the gates; it must keep its advisory path."""
    monkeypatch.setenv("UI_RE_HEADLESS_DRIVER", "1")
    monkeypatch.setattr(section_gate, "_ADVISORY_ONLY", False)
    section_gate._emit_block("driver advisory")
    captured = capsys.readouterr()

    assert captured.out.strip() == ""
    assert captured.err.strip() == "driver advisory"


# --- Repeat-signature path: full text once, one-line reminder on repeats ------

_REPEAT_REASON = (
    "⛔ UI-RE Gate: post-implement BLOCKED\n\n"
    "Incomplete items (2):\n  - required: runtime-env\n  - required: blank-viewport\n\n"
    "Run:\n  python -m ui_clone.gate tmp/ref/comp post-implement\n\n"
    "Goal Card: comp\n" + "Next action: long goal card text. " * 40
)


def _emit(
    capsys: pytest.CaptureFixture[str], ref_dir: Path, prefix: str = ""
) -> dict[str, object]:
    section_gate._emit_block(
        _REPEAT_REASON, ref_dir, section_gate._block_signature(_REPEAT_REASON), prefix
    )
    data = json.loads(capsys.readouterr().out.strip())
    assert isinstance(data, dict)
    return data


@pytest.fixture
def ledger(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("UI_RE_HEADLESS_DRIVER", raising=False)
    monkeypatch.delenv("UI_RE_STOP_RETRY_CAP", raising=False)
    monkeypatch.setattr(section_gate, "_ADVISORY_ONLY", False)
    monkeypatch.setattr(section_gate, "_BLOCK_LEDGER", (project, "sid-repeat"))
    ref_dir = project / "tmp" / "ref" / "comp"
    ref_dir.mkdir()
    return ref_dir


def test_repeat_signature_emits_short_reminder_that_still_blocks(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _emit(capsys, ledger)
    assert first == {"decision": "block", "reason": _REPEAT_REASON}

    second = _emit(capsys, ledger)
    assert second["decision"] == "block", "a repeat must still block"
    reason = str(second["reason"])
    assert "\n" not in reason, "repeat reminder must be one line"
    assert "post-implement BLOCKED" in reason
    assert f"python -m ui_clone.goal {ledger}" in reason
    # The failing items ride along so the agent need not re-run the gate.
    assert "required: runtime-env" in reason
    assert "required: blank-viewport" in reason
    # Size ceiling so the reminder cannot regrow into the full card.
    assert len(reason.split()) <= section_gate._REPEAT_REASON_MAX_WORDS
    assert len(reason.split()) < len(_REPEAT_REASON.split()) // 4


def test_repeat_reminder_keeps_retry_cap_semantics(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Short text never changes the verdict: cap blocks, then the full handback."""
    cap = section_gate._stop_retry_cap()
    for _ in range(cap):
        assert _emit(capsys, ledger)["decision"] == "block"
    final = _emit(capsys, ledger)
    assert "decision" not in final
    message = str(final["systemMessage"])
    assert "UNFINISHED" in message
    assert "runtime-env" in message, "handback must carry the full failure list"


def test_changed_signature_emits_full_text_again(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _emit(capsys, ledger)
    _emit(capsys, ledger)
    changed = _REPEAT_REASON.replace("  - required: blank-viewport\n", "")
    section_gate._emit_block(changed, ledger, section_gate._block_signature(changed))
    data = json.loads(capsys.readouterr().out)
    assert data == {"decision": "block", "reason": changed}


def test_forget_session_after_compact_restores_full_text(
    project: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PostCompact drops the earlier full text from context, so show it again."""
    from ui_clone.hooks import _stop_repeat

    _emit(capsys, ledger)
    assert "\n" not in str(_emit(capsys, ledger)["reason"])
    _stop_repeat.forget_session(project, "sid-repeat")
    again = _emit(capsys, ledger)
    assert again == {"decision": "block", "reason": _REPEAT_REASON}
    # Forgetting shown-state must not reset the retry budget.
    assert 3 in section_gate._read_stop_attempts(project).values()


def test_repeat_reminder_keeps_continuation_prefix(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prefix = "⛔ UI-RE continuation one-shot is arming\n\nCall CronCreate exactly once.\n\n"
    _emit(capsys, ledger, prefix)
    second = str(_emit(capsys, ledger, prefix)["reason"])
    assert second.startswith(prefix)
    assert "post-implement BLOCKED" in second


def test_repeat_reminder_lists_failing_gates_within_word_cap() -> None:
    long_items = "".join(
        f"  - required: some-very-long-failing-check-label-number-{i} with words\n"
        for i in range(12)
    )
    reason = f"⛔ UI-RE Gate: post-implement BLOCKED\n\nIncomplete items (12):\n{long_items}"
    line = section_gate._repeat_block_reason(reason, Path("tmp/ref/comp"))
    assert "\n" not in line
    assert "Failing: required: some-very-long-failing" in line
    assert len(line.split()) <= section_gate._REPEAT_REASON_MAX_WORDS

    stamp = (
        "⛔ UI-RE Verify-stamp gate: non-canonical stamp for tmp/ref/comp\n\n"
        "missing required gate evidence: boundary, font-parity.\n"
    )
    stamp_line = section_gate._repeat_block_reason(stamp, Path("tmp/ref/comp"))
    assert "Failing: boundary; font-parity." in stamp_line
