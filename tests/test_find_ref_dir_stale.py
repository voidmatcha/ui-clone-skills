"""find_ref_dir must ignore stale (abandoned/dead-run) .ui-re-active markers.

Regression for hooks firing "out of context" off weeks-old markers left in
sibling/scratch dirs: find_ref_dir picked the newest marker regardless of age,
so any hook routing through it treated a dead run as the active task.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ui_clone.hooks._common import find_ref_dir, stale_seconds


def _marker(root: Path, name: str, age_s: float = 0.0) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    m = d / ".ui-re-active"
    m.touch()
    if age_s:
        t = time.time() - age_s
        os.utime(m, (t, t))
    return d


def test_fresh_marker_beats_stale(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    root.mkdir(parents=True)
    _marker(root, "stale", age_s=stale_seconds() + 3600)
    fresh = _marker(root, "fresh", age_s=0.0)
    assert find_ref_dir(root) == fresh


def test_all_stale_markers_yield_no_active_ref(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    root.mkdir(parents=True)
    _marker(root, "old1", age_s=stale_seconds() + 3600)
    _marker(root, "old2", age_s=stale_seconds() * 5)
    # No extracted.json anywhere -> no fallback -> None (no spurious active task).
    assert find_ref_dir(root) is None


def test_stale_threshold_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_RE_STALE_DAYS", "0.001")  # ~86s
    root = tmp_path / "tmp" / "ref"
    root.mkdir(parents=True)
    _marker(root, "c", age_s=600)  # 10 min > 86 s -> stale
    assert find_ref_dir(root) is None
