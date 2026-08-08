"""dssim leniency cap — pass-by-dssim/perceptual disallowed at extreme AE.

Evidence class (realfood e2e-8): dga_broken_system passed `pass-by-perceptual`
at AE/Mpx 83593 — ~42x the 2000 threshold. dssim/perceptual leniency exists to
absorb font-AA and idle drift, not to wave through sections whose pixel
divergence is an order of magnitude past the threshold. Above the cap, only an
explicit fresh visual-judge confirmation (sections/<name>-judge.json with
verdict PASS, written by the visual-debug-reviewer subagent) may keep the
leniency path open.
"""

import subprocess
import time
from pathlib import Path

from ._helpers import _project_root

LIB = "skills/visual-debug/scripts/lib/dssim-cap.sh"


def _allows(ae: int, thr: int, mult: int, judge: Path, crop: Path) -> bool:
    lib = _project_root() / LIB
    r = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{lib}" && dssim_cap_allows "$@"',
            "_",
            str(ae),
            str(thr),
            str(mult),
            str(judge),
            str(crop),
        ],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def test_within_cap_is_allowed(tmp_path: Path) -> None:
    judge = tmp_path / "x-judge.json"  # absent
    crop = tmp_path / "x.png"
    assert _allows(19999, 2000, 10, judge, crop)


def test_above_cap_without_judge_is_denied(tmp_path: Path) -> None:
    judge = tmp_path / "x-judge.json"  # absent
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    assert not _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_fresh_pass_judge_is_allowed(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    time.sleep(0.05)
    judge = tmp_path / "x-judge.json"
    judge.write_text('{"verdict": "PASS", "by": "visual-debug-reviewer"}')
    assert _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_fail_judge_is_denied(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    judge = tmp_path / "x-judge.json"
    judge.write_text('{"verdict": "FAIL"}')
    assert not _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_stale_judge_is_denied(tmp_path: Path) -> None:
    """A judge verdict that predates the current impl crop is stale — the
    crop changed after the human/LLM looked at it."""
    judge = tmp_path / "x-judge.json"
    judge.write_text('{"verdict": "PASS"}')
    time.sleep(1.1)  # mtime granularity
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    assert not _allows(83593, 2000, 10, judge, crop)


def test_section_compare_wires_cap_into_both_leniency_branches() -> None:
    sc = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    ).read_text(encoding="utf-8")
    assert "dssim-cap.sh" in sc, "section-compare must source the cap lib"
    assert sc.count("dssim_cap_allows") >= 2, (
        "cap must guard BOTH pass-by-dssim and pass-by-perceptual branches"
    )
