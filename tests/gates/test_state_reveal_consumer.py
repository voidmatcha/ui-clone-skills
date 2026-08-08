"""batch-7 ITEM 4 — state-reveal provenance + threshold-band consumer checks.

A state-reveal `pass` artifact must rest on a live state-sweep (A4: a
hand-authored artifact has no runtimeScanned) and the EFFECTIVE thresholds it
records must lie inside the allowed band (A5: env-tuned RATIO=0.01 /
MIN_CONTENT=100 cannot mint a pass even if the artifact claims it). Recreates
/tmp/adv2-state-reveal run_env.py at the consumer layer."""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.base import Gate

from ._helpers import _post_implement_baseline


def _plan_with_state_reveal(ref: Path) -> None:
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "state-reveal", "produces": "state-reveal.json",
             "reason": "active-state reveal end-state", "severity": "block"},
        ],
    }))


def _artifact(ref: Path, **over: object) -> None:
    data: dict = {
        "schemaVersion": 1,
        "status": "pass",
        "runtimeScanned": True,
        "effectiveRevealRatio": 0.5,
        "effectiveMinContentPx": 12.0,
        "rows": [],
        "unmeasured": [],
    }
    data.update(over)
    (ref / "state-reveal.json").write_text(json.dumps(data))


def _impl_root(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    (impl / "package.json").write_text('{"name":"state-fixture"}', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text("export default function App(){return <main />}\n", encoding="utf-8")
    monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(impl.resolve()))
    return impl


def _bound_artifact(ref: Path, impl: Path, **over: object) -> None:
    """A pass artifact with a live-scan receipt bound to the impl tree — the
    batch-9 ITEM 3 provenance mirror of the hover-fallback receipt."""
    receipt = impl / ".state-reveal-scan-receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    over.setdefault("scanReceipt", str(receipt.resolve()))
    _artifact(ref, **over)


def _row(ref: Path):  # type: ignore[no-untyped-def]
    results = Gate(ref).gate_post_implement()
    return next(r for r in results if "state-reveal" in r.label)


def test_state_reveal_pass_with_scan_and_inband_passes(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    _bound_artifact(ref, impl)
    assert _row(ref).status == "pass", _row(ref).message


def test_state_reveal_runtime_true_no_impl_root_rejected(tmp_path: Path) -> None:
    # batch-9 ITEM 3 (Codex BLOCKER): runtimeScanned=true but no impl tree
    # resolves — the live-scan receipt cannot be bound to the active impl, so a
    # forged pass with no discoverable impl_root must not stand (mirror hover A4).
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref, with_impl=False)
    _plan_with_state_reveal(ref)
    _artifact(ref)  # runtimeScanned True, no impl_root, no receipt
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)


def test_state_reveal_runtime_true_no_receipt_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # batch-9 ITEM 3: the impl tree resolves but the artifact records no scan
    # receipt — the path-check requires a recorded receipt to bind (mirror hover
    # A4b), so a self-attested runtimeScanned with no receipt is rejected.
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    _impl_root(tmp_path, monkeypatch)
    _artifact(ref, scanReceipt=None)
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)


def test_state_reveal_hand_authored_no_runtime_scan_rejected(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    _artifact(ref, runtimeScanned=False)  # A4
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "state-sweep" in row.message.lower() or "runtime" in row.message.lower() or "path-check" in row.message.lower()


def test_state_reveal_out_of_band_ratio_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    _bound_artifact(ref, impl, effectiveRevealRatio=0.01)  # A5
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "band" in row.message.lower()


def test_state_reveal_out_of_band_min_content_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    _bound_artifact(ref, impl, effectiveMinContentPx=100)  # A5
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)


def test_state_reveal_omitted_effective_fields_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # batch-8 ITEM 8: a pass that OMITS the effective thresholds must fail
    # (fail-closed). Before the fix the band-check only fired on isinstance(...,
    # int|float), so absent fields slipped through. _artifact always seeds the
    # fields, so write the dict directly without them.
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    receipt = impl / ".state-reveal-scan-receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    (ref / "state-reveal.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "runtimeScanned": True,
        "scanReceipt": str(receipt.resolve()),
        "rows": [{"status": "ok"}], "unmeasured": [],
    }))
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "numeric" in row.message.lower() or "absent" in row.message.lower()


def test_state_reveal_stringified_effective_fields_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # batch-8 ITEM 8: stringifying the thresholds ("0.01") dodged the isinstance
    # int|float band-check and passed before the fix — must fail now.
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_state_reveal(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    _bound_artifact(ref, impl, effectiveRevealRatio="0.01", effectiveMinContentPx="12.0")
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "numeric" in row.message.lower() or "absent" in row.message.lower()


def test_status_less_hover_fallback_artifact_rejected(tmp_path: Path) -> None:
    # batch-8 ITEM 8 minor: hover-fallback joins STATUS_REQUIRED, so a status-
    # less hover-fallback.json no longer vacuously passes as "artifact present".
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hover-fallback", "produces": "hover-fallback.json",
             "reason": "hover cascade fallback", "severity": "block"},
        ],
    }))
    (ref / "hover-fallback.json").write_text(json.dumps({"schemaVersion": 1, "notes": "no status"}))
    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "hover-fallback" in r.label)
    assert row.status == "fail", (row.status, row.message)
    assert "status" in row.message.lower()
