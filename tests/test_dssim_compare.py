"""dssim-compare.sh width-mismatch guard.

A width mismatch between the impl and ref capture is "fixed" inside the script
by a non-uniform horizontal stretch (`convert -resize WxH!`), which shifts every
column boundary and fabricates a registration penalty unrelated to fidelity. The
score from a stretched impl is therefore NOT a fidelity signal. This regression
locks the guard that flags such a row `WIDTH-MISMATCH (invalid)` instead of
emitting it as an authoritative PASS/FAIL — the exact trap behind the eBay
phantom-regression episode, where a distorted score was trusted. Also closes the
long-standing gap that dssim-compare.sh shipped with no test at all.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "visual-debug" / "scripts" / "dssim-compare.sh"

_TOOLS_PRESENT = all(shutil.which(t) for t in ("dssim", "convert", "identify"))
_skip = pytest.mark.skipif(
    not _TOOLS_PRESENT, reason="dssim/imagemagick not available"
)


def _png(path: Path, w: int, h: int, spec: str = "xc:white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["convert", "-size", f"{w}x{h}", spec, str(path)], check=True
    )


def _run(dir_: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(dir_), "0.50"],
        capture_output=True, text=True, timeout=60, check=False,
    )


@_skip
def test_matched_width_pair_scores_normally(tmp_path: Path) -> None:
    """Same-width identical captures compare normally and pass (exit 0)."""
    _png(tmp_path / "static/ref/sectionA.png", 200, 300)
    _png(tmp_path / "static/impl/sectionA.png", 200, 300)
    proc = _run(tmp_path)
    assert "WIDTH-MISMATCH" not in proc.stdout, proc.stdout
    assert "1/1 PASS, 0 FAIL, 0 INVALID" in proc.stdout, proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr


@_skip
def test_width_mismatch_is_flagged_invalid_not_passed(tmp_path: Path) -> None:
    """A width mismatch yields a tiny (near-zero) stretched score that would
    otherwise read as a perfect PASS; it must be flagged INVALID and force a
    non-zero exit so the distorted number never silently reports success."""
    _png(tmp_path / "static/ref/sectionB.png", 200, 300, "gradient:black-white")
    _png(tmp_path / "static/impl/sectionB.png", 160, 300, "gradient:black-white")
    proc = _run(tmp_path)
    assert "WIDTH-MISMATCH (invalid)" in proc.stdout, proc.stdout
    assert "0/1 PASS, 0 FAIL, 1 INVALID" in proc.stdout, proc.stdout
    # would-be-PASS score must NOT have been counted as a pass
    assert "1/1 PASS" not in proc.stdout, proc.stdout
    assert proc.returncode == 1, proc.stdout + proc.stderr


@_skip
def test_vertical_only_mismatch_stays_valid(tmp_path: Path) -> None:
    """A height-only mismatch (same width) is expected for anchored section
    strips and must still score normally — the guard is width-specific."""
    _png(tmp_path / "static/ref/sectionC.png", 200, 300)
    _png(tmp_path / "static/impl/sectionC.png", 200, 260)
    proc = _run(tmp_path)
    assert "WIDTH-MISMATCH" not in proc.stdout, proc.stdout
    assert "0 INVALID" in proc.stdout, proc.stdout


@_skip
def test_static_scroll_layout_wins_over_legacy_static(tmp_path: Path) -> None:
    """batch-scroll.sh (0.8.15) moved its own captures to
    static/scroll/{ref,impl} so it stops clobbering capture.sh's
    static/ref/section-*.png baseline; dssim-compare.sh must prefer that
    layout when both exist rather than silently falling back to whatever
    stale content sits in the legacy static/ref path."""
    # Legacy path: a mismatched pair that would score INVALID if read.
    _png(tmp_path / "static/ref/0pct.png", 200, 300)
    _png(tmp_path / "static/impl/0pct.png", 100, 300)
    # New path: a matched pair that scores normally.
    _png(tmp_path / "static/scroll/ref/0pct.png", 200, 300)
    _png(tmp_path / "static/scroll/impl/0pct.png", 200, 300)
    proc = _run(tmp_path)
    assert "WIDTH-MISMATCH" not in proc.stdout, proc.stdout
    assert "1/1 PASS, 0 FAIL, 0 INVALID" in proc.stdout, proc.stdout
