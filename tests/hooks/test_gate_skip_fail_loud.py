"""Fail-LOUD run_gate + gate_skip_blocker (hook review Tier 1).

run_gate stays fail-OPEN (a host missing uv/Pillow must not be bricked) but a
skipped gate must no longer read as a silent clean pass: it returns a `skipped`
marker, warns, and records the skip so closeout/push refuses a terminal `done`.
A gate that later actually runs clears its own entry (run-scoping), and the user
can release the block with `gateSkipAck`.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from ui_clone.hooks import _common


def test_run_gate_skip_is_loud_and_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(_common.subprocess, "run", boom)
    res = _common.run_gate(tmp_path, "section-compare")

    assert res["passed"] is True, "fail-open preserved (no brick on dep-poor host)"
    assert res["skipped"] is True, "skip must be marked, not a silent clean pass"
    log = (tmp_path / ".gate-skip-log").read_text(encoding="utf-8")
    assert "gate=section-compare" in log


def test_run_gate_fails_closed_when_skip_unrecordable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(_common.subprocess, "run", boom)
    monkeypatch.setattr(_common, "_log_gate_skip", lambda *_a, **_k: False)

    res = _common.run_gate(tmp_path, "section-compare")

    assert res["passed"] is False, "unrecordable skip must fail closed"
    assert res["skip_record_failed"] is True
    assert res["fail_count"] == 1
    failures = res["failures"]
    assert isinstance(failures, list)
    first = failures[0]
    assert isinstance(first, dict)
    assert first["label"] == "section-compare"
    assert "could not be durably recorded" in first["reason"]


def test_run_gate_unrecordable_skip_released_by_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(_common.subprocess, "run", boom)
    monkeypatch.setattr(_common, "_log_gate_skip", lambda *_a, **_k: False)
    (tmp_path / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "gateSkipAck": "dependency-poor host accepted"}),
        encoding="utf-8",
    )

    res = _common.run_gate(tmp_path, "section-compare")

    assert res["passed"] is True
    assert res["skipped"] is True


def test_log_gate_skip_reports_durability(tmp_path: Path) -> None:
    assert _common._log_gate_skip(tmp_path, "spec", "FileNotFoundError") is True
    log = (tmp_path / ".gate-skip-log").read_text(encoding="utf-8")
    assert "gate=spec reason=FileNotFoundError" in log


def test_log_gate_skip_returns_false_on_unwritable_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert _common._log_gate_skip(missing, "spec", "x") is False


def test_run_gate_success_clears_prior_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".gate-skip-log").write_text(
        "2026-01-01T00:00:00Z gate=section-compare reason=FileNotFoundError\n",
        encoding="utf-8",
    )
    ok = types.SimpleNamespace(
        stdout='{"passed": true, "fail_count": 0, "failures": []}',
        returncode=0,
        stderr="",
    )
    monkeypatch.setattr(_common.subprocess, "run", lambda *_a, **_k: ok)

    res = _common.run_gate(tmp_path, "section-compare")

    assert res["passed"] is True and "skipped" not in res
    # The only entry was for this gate → log self-heals away entirely.
    assert not (tmp_path / ".gate-skip-log").exists()


def test_clear_gate_skip_keeps_other_gates(tmp_path: Path) -> None:
    (tmp_path / ".gate-skip-log").write_text(
        "ts gate=bundle reason=x\nts gate=spec reason=y\n", encoding="utf-8"
    )
    _common._clear_gate_skip(tmp_path, "bundle")
    remaining = (tmp_path / ".gate-skip-log").read_text(encoding="utf-8")
    assert "gate=spec reason=y" in remaining
    assert "gate=bundle" not in remaining


def test_gate_skip_blocker_none_when_clean(tmp_path: Path) -> None:
    assert _common.gate_skip_blocker(tmp_path) is None  # no log
    (tmp_path / ".gate-skip-log").write_text("\n", encoding="utf-8")
    assert _common.gate_skip_blocker(tmp_path) is None  # empty log


def test_gate_skip_blocker_fires_and_names_gates(tmp_path: Path) -> None:
    (tmp_path / ".gate-skip-log").write_text(
        "ts gate=bundle reason=FileNotFoundError\nts gate=spec reason=Timeout\n",
        encoding="utf-8",
    )
    blocker = _common.gate_skip_blocker(tmp_path)
    assert blocker is not None
    assert "bundle" in blocker and "spec" in blocker
    assert "not enforced" in blocker.lower()


def test_gate_skip_blocker_released_by_ack(tmp_path: Path) -> None:
    (tmp_path / ".gate-skip-log").write_text("ts gate=bundle reason=x\n", encoding="utf-8")
    (tmp_path / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "gateSkipAck": "user accepts un-enforced run"}),
        encoding="utf-8",
    )
    assert _common.gate_skip_blocker(tmp_path) is None


# ── H1: every canonical closeout path must consult the skip-ledger, not only the
# structural one. A fresh/valid stamp after an un-recovered fail-open skip must
# still block. (The structural path already did; these cover the three that did not.)


def _ref_with_impl(tmp_path: Path) -> Path:
    ref_dir = tmp_path / "tmp" / "ref" / "comp"
    ref_dir.mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref_dir / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    return ref_dir


def test_verify_stamp_closeout_blocks_on_unrecovered_skip(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _enforce_verify_stamp

    ref_dir = _ref_with_impl(tmp_path)
    (ref_dir / ".gate-skip-log").write_text(
        "ts gate=section-compare reason=FileNotFoundError\n", encoding="utf-8"
    )
    block = _enforce_verify_stamp(ref_dir)
    assert block is not None
    assert "un-enforced gates" in block and "section-compare" in block


def test_canvas_replay_closeout_blocks_on_unrecovered_skip(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _enforce_canvas_replay_stamp

    ref_dir = _ref_with_impl(tmp_path)
    (ref_dir / ".gate-skip-log").write_text("ts gate=spec reason=Timeout\n", encoding="utf-8")
    block = _enforce_canvas_replay_stamp(ref_dir)
    assert block is not None
    assert "un-enforced gates" in block and "spec" in block


def test_verify_stamp_skip_block_released_by_ack(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _enforce_verify_stamp

    ref_dir = _ref_with_impl(tmp_path)
    (ref_dir / ".gate-skip-log").write_text("ts gate=spec reason=x\n", encoding="utf-8")
    (ref_dir / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "gateSkipAck": "user accepts"}), encoding="utf-8"
    )
    block = _enforce_verify_stamp(ref_dir)
    # ack releases the skip block; the path then proceeds to the (missing) stamp
    # check, so it still blocks but NOT on un-enforced gates.
    assert block is not None and "un-enforced gates" not in block


def test_canonical_stamp_problem_blocks_on_unrecovered_skip(tmp_path: Path) -> None:
    import datetime

    from ui_clone.pipeline_phases.verify import canonical_stamp_problem
    from ui_clone.state import POST_IMPL_VERIFY_GATES

    ref_dir = tmp_path / "tmp" / "ref" / "comp"
    ref_dir.mkdir(parents=True)
    (ref_dir / "verify-stamp.json").write_text(
        json.dumps(
            {
                "verifiedAt": datetime.datetime.now(datetime.UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "stampedBy": "pipeline.execute_verify",
                "gatesPassed": list(POST_IMPL_VERIFY_GATES),
            }
        ),
        encoding="utf-8",
    )
    # A valid, fresh stamp alone → satisfied.
    assert canonical_stamp_problem(ref_dir) is None
    # An un-recovered fail-open skip must flip goal --check-done to not-satisfied.
    (ref_dir / ".gate-skip-log").write_text(
        "ts gate=section-compare reason=FileNotFoundError\n", encoding="utf-8"
    )
    problem = canonical_stamp_problem(ref_dir)
    assert problem is not None
    assert "not satisfied" in problem and "section-compare" in problem
