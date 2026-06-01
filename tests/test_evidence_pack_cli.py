from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_evidence_pack_cli_writes_briefs_without_dumping_raw_dom(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "session": {"url": "https://example.com", "viewport": {"width": 1440, "height": 900}},
                "annotations": [
                    {
                        "id": "target",
                        "selector": "main h1",
                        "note": "Splash reveal timing is wrong.",
                        "bbox": {"x": 100, "y": 120, "width": 900, "height": 120},
                        "dom": "<h1>Very large raw subtree that should not be printed</h1>",
                        "computedStyle": {"fontSize": "96px", "opacity": "1", "unused": "drop"},
                        "timeline": [{"phase": "idle", "changed": True, "properties": ["opacity"]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "briefs"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ui_clone.evidence_pack",
            str(pack_path),
            "--out-dir",
            str(out_dir),
            "--max-chars",
            "1400",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "WORKER_BRIEF.md" in result.stdout
    assert "<h1>" not in result.stdout
    assert (out_dir / "WORKER_BRIEF.md").is_file()
    assert (out_dir / "CURRENT_STATE.json").is_file()


def test_evidence_pack_cli_rejects_missing_pack(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.evidence_pack", str(tmp_path / "missing.json")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "not found" in result.stderr
