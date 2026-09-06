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
    """With the budget spent the stop is allowed -- but never in silence."""
    monkeypatch.delenv("UI_RE_HEADLESS_DRIVER", raising=False)
    monkeypatch.setattr(section_gate, "_ADVISORY_ONLY", True)
    section_gate._emit_block("GATE: section-compare BLOCKED\n  - hero mismatch")
    captured = capsys.readouterr()

    assert captured.out.strip() == "", "advisory mode must not emit a block decision"
    assert "UNFINISHED" in captured.err
    assert "INCOMPLETE" in captured.err
    assert "hero mismatch" in captured.err, "the user must be told WHICH gate failed"


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
