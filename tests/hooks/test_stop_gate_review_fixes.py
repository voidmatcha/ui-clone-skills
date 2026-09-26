"""Stop-hook fail-open regressions: pre-generation impl roots and malformed stamps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ui_clone.hooks.section_gate as mod
from ui_clone.state import PipelineState


def _ref_with_asset_only_impl(tmp_path: Path, gate: str) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "public" / "a.png").write_bytes(b"\x89PNG")
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.current_gate = gate
    state.save(ref)
    return ref


def test_asset_only_impl_root_still_runs_current_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An impl root holding only Step 6e assets must not skip current-gate
    enforcement: the stamp enforcer's pre-generation None is not a release."""
    ref = _ref_with_asset_only_impl(tmp_path, "extraction")
    calls: list[str] = []

    def fake_run_gate(ref_dir: Path, gate: str) -> dict[str, object]:
        calls.append(gate)
        return {"passed": False, "output": "extraction FAIL"}

    monkeypatch.setattr(mod, "_run_gate", fake_run_gate)
    reason = mod._enforce_ref_dir(ref)
    assert calls == ["extraction"]
    assert reason is not None


def test_asset_only_impl_root_gate_pass_is_not_stop_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _ref_with_asset_only_impl(tmp_path, "extraction")
    monkeypatch.setattr(mod, "_run_gate", lambda ref_dir, gate: {"passed": True})
    assert mod._enforce_ref_dir(ref) is not None


@pytest.mark.parametrize("raw", ["[]", '"x"', '{"verifiedAt": 5}', "{not json"])
def test_malformed_verify_stamp_fails_closed(tmp_path: Path, raw: str) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    state = PipelineState.load(ref)
    state.impl_root = str(impl)
    state.current_gate = "post-implement"
    state.save(ref)
    (ref / "verify-stamp.json").write_text(raw)
    reason = mod._enforce_verify_stamp(ref)
    assert reason is not None and "malformed stamp" in reason, reason


def test_load_stamp_rejects_non_object_root(tmp_path: Path) -> None:
    stamp = tmp_path / "s.json"
    stamp.write_text(json.dumps([]))
    assert isinstance(mod._load_stamp(stamp), str)
