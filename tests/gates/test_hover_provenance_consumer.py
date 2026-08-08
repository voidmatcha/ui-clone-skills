"""hover-fallback provenance (batch-6 ITEM 4 + batch-7 ITEM 4b): a `pass`
artifact must rest on a live hover scan, and the env flag is no longer
sufficient — the probe writes a scan receipt INSIDE the impl tree and the
consumer binds it to impl_root (like junk-token's implSrcDir). A self-attested
runtimeScanned=true with no resolvable impl_root or no receipt is rejected.
Recreates /tmp/adv2-hover at the consumer layer."""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.base import Gate

from ._helpers import _post_implement_baseline


def _plan_with_hover(ref: Path) -> None:
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hover-fallback", "produces": "hover-fallback.json",
             "reason": "per-entry hover coverage", "severity": "block"},
        ],
    }))


def _artifact(ref: Path, *, status: str, runtime_scanned: bool,
              scan_receipt: str | None = None) -> None:
    (ref / "hover-fallback.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": status,
        "runtimeScanned": runtime_scanned,
        "scanReceipt": scan_receipt,
        "entries": [],
        "coverage": {"measured": 0, "verified": 0, "staticVerified": 0, "failed": 0},
    }))


def _impl_root(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    (impl / "package.json").write_text('{"name":"hover-fixture"}', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text("export default function App(){return <main />}\n", encoding="utf-8")
    monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(impl.resolve()))
    return impl


def _row(ref: Path):  # type: ignore[no-untyped-def]
    return next(r for r in Gate(ref).gate_post_implement() if "hover-fallback" in r.label)


def test_hover_pass_with_scan_receipt_and_impl_root_passes(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_hover(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    receipt = impl / ".hover-scan-receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    _artifact(ref, status="pass", runtime_scanned=True, scan_receipt=str(receipt.resolve()))
    row = _row(ref)
    assert row.status == "pass", (row.status, row.message)


def test_hover_pass_without_runtime_scan_is_rejected(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_hover(ref)
    _artifact(ref, status="pass", runtime_scanned=False)
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "runtime" in row.message.lower() or "scan" in row.message.lower() or "path-check" in row.message.lower()


def test_hover_env_spoof_no_impl_root_rejected(tmp_path: Path) -> None:
    # Attack 4: runtimeScanned=true but no impl tree resolves — the scan cannot
    # be bound to the active impl, so the self-attested flag cannot mint a pass.
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref, with_impl=False)
    _plan_with_hover(ref)
    _artifact(ref, status="pass", runtime_scanned=True)  # no impl_root, no receipt
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
    assert "impl" in row.message.lower()


def test_hover_runtime_true_without_receipt_rejected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Attack 4b: impl tree resolves but the artifact records no scan receipt —
    # the path-check requires a recorded receipt to bind, so it is rejected.
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_hover(ref)
    _impl_root(tmp_path, monkeypatch)
    _artifact(ref, status="pass", runtime_scanned=True, scan_receipt=None)
    row = _row(ref)
    assert row.status == "fail", (row.status, row.message)
