"""Scroll completion must reach the document end beyond delayed scroll gates."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "mode",
    ["static", "growing", "locked", "locked-no-footer", "locked-opacity-footer"],
)
def test_document_endpoint(
    mode: str, tmp_path: Path, http_server: str, repo_root: Path
) -> None:
    session = f"scroll-end-{uuid.uuid4().hex[:12]}"
    script = repo_root / "skills/visual-debug/scripts/scroll-end-completion-check.sh"
    result = subprocess.run(
        ["bash", str(script), session,
         f"{http_server}scroll-end-gated.html?mode={mode}", str(tmp_path)],
        env={**os.environ, "VIEWPORTS": "1440x900", "WAIT_MS": "0"},
        capture_output=True, text=True, timeout=60,
    )
    artifact = json.loads((tmp_path / "scroll-completion.json").read_text())
    endpoint = artifact["viewports"][0]["endpoint"]
    if mode.startswith("locked"):
        assert result.returncode == 2, result.stdout + result.stderr
        assert artifact["status"] == "error"
        assert endpoint["reached"] is False
        if mode == "locked-no-footer":
            assert endpoint["clippedLandmarks"] == ["main"]
        elif mode == "locked-opacity-footer":
            assert endpoint["clippedLandmarks"] == ["footer"]
        else:
            assert endpoint["clippedLandmarks"] == ["main", "footer"]
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert artifact["status"] == "pass"
        assert endpoint["reached"] is True
        assert endpoint["scrollHeight"] == 5200
        assert endpoint["scrollY"] == 4300
        assert endpoint["remaining"] == 0
        assert endpoint["clippedLandmarks"] == []
