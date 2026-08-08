from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> ModuleType:
    key = "_capacity_probe_test_module"
    if key in sys.modules:
        return sys.modules[key]
    path = ROOT / "scripts" / "verify" / "capacity.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def test_capacity_report_recommends_serial_when_memory_is_tight() -> None:
    mod = _load_module()
    report = mod.build_capacity_report(total_mb=8192, available_mb=5000, browser_budget_mb=4400, reserve_mb=4096)
    assert report.maxConcurrentBrowsers == 1
    assert report.recommendedWaveSize == 1
    assert report.serialBackendRequired is True
    assert report.leanResources is True


def test_capacity_report_caps_wave_size_for_large_machines() -> None:
    mod = _load_module()
    report = mod.build_capacity_report(total_mb=65536, available_mb=50000, browser_budget_mb=4400, reserve_mb=4096)
    assert report.maxConcurrentBrowsers >= 3
    assert report.recommendedWaveSize == 3
    assert report.serialBackendRequired is False


def test_capacity_cli_writes_json(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    mod = _load_module()
    out = tmp_path / "capacity.json"
    rc = mod.main(["--total-mb", "16384", "--available-mb", "12000", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["source"] == "scripts/verify/capacity.py"
    assert json.loads(capsys.readouterr().out)["recommendedWaveSize"] >= 1


def test_capacity_check_wrapper_materializes_status_artifact(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    script = ROOT / "scripts" / "verify" / "capacity-check.sh"

    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((ref / "capacity-report.json").read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["recommendedWaveSize"] >= 1
