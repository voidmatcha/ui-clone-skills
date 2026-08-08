from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "preview-runtime-health-check.sh"
PLAN = ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
DISPATCH = ROOT / "scripts" / "verify" / "run-required-checks.sh"


def test_preview_runtime_health_script_is_registered_in_verification_plan() -> None:
    text = PLAN.read_text(encoding="utf-8")

    assert "preview-runtime-health" in text
    assert "skills/visual-debug/scripts/preview-runtime-health-check.sh" in text
    assert "preview-runtime-health.json" in text
    assert "same-origin" in text
    assert "horizontal overflow" in text
    assert "scroll-state" in text


def test_verification_plan_emits_preview_runtime_health_row(tmp_path: Path) -> None:
    (tmp_path / "extracted.json").write_text('{"sections":[]}', encoding="utf-8")
    (tmp_path / "transition-spec.json").write_text('{"transitions":[]}', encoding="utf-8")

    subprocess.run(["bash", str(PLAN), str(tmp_path)], check=True, cwd=ROOT)

    payload = json.loads((tmp_path / "verification-plan.json").read_text(encoding="utf-8"))
    rows = [
        row
        for value in payload.values()
        if isinstance(value, list)
        for row in value
        if isinstance(row, dict) and row.get("id") == "preview-runtime-health"
    ]

    assert len(rows) == 1
    assert rows[0]["produces"] == "preview-runtime-health.json"
    assert rows[0]["severity"] == "block"
    assert rows[0].get("dependsOn") == ["runtime-env"]


def test_run_required_checks_can_dispatch_preview_runtime_health() -> None:
    text = (ROOT / "scripts" / "verify" / "build_required_dispatch.py").read_text(encoding="utf-8")

    assert '"preview-runtime-health-check.sh":' in text
    assert "{session}-prh {ref_url} {impl_url} {ref_dir}" in text


def test_preview_runtime_health_script_contract() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)

    text = SCRIPT.read_text(encoding="utf-8")
    assert "preview-runtime-health.json" in text
    assert "document.documentElement.scrollWidth" in text
    assert "headAssetOnReferenceOrigin" in text
    assert "scrollTransitionParity" in text
    assert "agent-browser" in text
    assert "run_with_timeout.py" in text
