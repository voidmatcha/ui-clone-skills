"""Review-1 MAJOR 3: the junk-token artifact must not pass on static coverage
alone. The runtime DOM scan catches template-string junk that only
materializes live; a "pass" with runtimeScanned=false is incomplete coverage
and the post-implement consumer must reject it."""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.base import Gate

from ._helpers import _post_implement_baseline


def _plan_with_junk_token(ref: Path) -> None:
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "junk-token", "produces": "junk-token.json",
             "reason": "serialization junk", "severity": "block"},
        ],
    }))


def _artifact(ref: Path, *, status: str, runtime_scanned: bool,
              impl_src_dir: str = "/tmp/impl/src") -> None:
    (ref / "junk-token.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": status,
        "staticFindings": [],
        "runtimeFindings": [],
        "runtimeScanned": runtime_scanned,
        "implSrcDir": impl_src_dir,
    }))


def _impl_root(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    """Create a resolvable impl tree and point the resolver at it."""
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    (impl / "package.json").write_text('{"name":"junk-fixture"}', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text("export default function App(){return <main />}\n", encoding="utf-8")
    monkeypatch.setenv("UI_CLONE_IMPL_ROOT", str(impl.resolve()))
    return impl


def test_pass_with_runtime_scanned_true_passes(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_junk_token(ref)
    impl = _impl_root(tmp_path, monkeypatch)
    _artifact(ref, status="pass", runtime_scanned=True,
              impl_src_dir=str((impl / "src").resolve()))
    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "junk-token" in r.label)
    assert row.status == "pass", (row.status, row.message)


def test_pass_runtime_scanned_without_impl_root_is_rejected(tmp_path: Path) -> None:
    """tools batch-6 ITEM 5(c) / Attack 3a: a forged junk-token pass artifact
    claiming a runtime scan, run against a standalone ref dir where no impl_root
    resolves, must NOT pass — the path/staleness validation is otherwise skipped
    entirely when impl_root is None."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref, with_impl=False)
    _plan_with_junk_token(ref)
    _artifact(ref, status="pass", runtime_scanned=True)  # no impl_root resolves
    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "junk-token" in r.label)
    assert row.status == "fail", (row.status, row.message)
    assert "impl" in row.message.lower()


def test_pass_without_runtime_scan_is_rejected(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_junk_token(ref)
    _artifact(ref, status="pass", runtime_scanned=False)
    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "junk-token" in r.label)
    assert row.status == "fail", (row.status, row.message)
    assert "runtime" in row.message.lower()


def test_warn_without_runtime_scan_stays_warn(tmp_path: Path) -> None:
    """The script itself downgrades to warn when the runtime scan could not
    run — the consumer must not double-penalize an honest warn."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    _plan_with_junk_token(ref)
    _artifact(ref, status="warn", runtime_scanned=False)
    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "junk-token" in r.label)
    assert row.status == "warn", (row.status, row.message)
