"""Consumer checks for dynamic-behavior-parity artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.base import Gate

from ._helpers import _post_implement_baseline


def test_status_less_dynamic_behavior_artifact_rejected(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {
                "id": "dynamic-behavior-parity",
                "produces": "dynamic-behavior-parity.json",
                "reason": "runtime transition parity",
                "severity": "block",
            },
        ],
    }))
    (ref / "dynamic-behavior-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "regions": [],
    }))

    results = Gate(ref).gate_post_implement()
    row = next(r for r in results if "dynamic-behavior-parity" in r.label)

    assert row.status == "fail", (row.status, row.message)
    assert "status" in row.message.lower()
