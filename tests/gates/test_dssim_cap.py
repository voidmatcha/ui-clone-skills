"""dssim leniency cap — pass-by-dssim/perceptual disallowed at extreme AE.

Evidence class (realfood e2e-8): dga_broken_system passed `pass-by-perceptual`
at AE/Mpx 83593 — ~42x the 2000 threshold. dssim/perceptual leniency exists to
absorb font-AA and idle drift, not to wave through sections whose pixel
divergence is an order of magnitude past the threshold. Above the cap, only an
explicit visual-judge confirmation may keep the leniency path open.

P0·2: that confirmation must be BOUND to the exact crop bytes reviewed —
sections/<name>-judge.json with verdict PASS, an implSha256 re-verified against
the live crop bytes at read time (mtime is forgeable), and a non-empty
rationale. A bare {"verdict":"PASS"} line, a mismatched/absent hash, or an empty
rationale no longer overrides the cap.
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

from ._helpers import _project_root

LIB = "skills/visual-debug/scripts/lib/dssim-cap.sh"


def _allows(
    ae: int, thr: int, mult: int, judge: Path, crop: Path, ref: Path | None = None
) -> bool:
    lib = _project_root() / LIB
    argv = [
        "bash",
        "-c",
        f'source "{lib}" && dssim_cap_allows "$@"',
        "_",
        str(ae),
        str(thr),
        str(mult),
        str(judge),
        str(crop),
    ]
    if ref is not None:
        argv.append(str(ref))
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _pass_judge(crop: Path, ref: Path | None = None, rationale: str = "looks identical") -> dict:
    d: dict = {
        "verdict": "PASS",
        "implSha256": _sha256(crop),
        "rationale": rationale,
        "by": "visual-debug-reviewer",
    }
    if ref is not None:
        d["refSha256"] = _sha256(ref)
    return d


def test_within_cap_is_allowed(tmp_path: Path) -> None:
    judge = tmp_path / "x-judge.json"  # absent
    crop = tmp_path / "x.png"
    assert _allows(19999, 2000, 10, judge, crop)


def test_above_cap_without_judge_is_denied(tmp_path: Path) -> None:
    judge = tmp_path / "x-judge.json"  # absent
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    assert not _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_crop_bound_pass_is_allowed(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png-bytes")
    judge = tmp_path / "x-judge.json"
    judge.write_text(json.dumps(_pass_judge(crop)))
    assert _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_ref_pair_bound_pass_is_allowed(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"impl-bytes")
    ref = tmp_path / "x-ref.png"
    ref.write_bytes(b"ref-bytes")
    judge = tmp_path / "x-judge.json"
    judge.write_text(json.dumps(_pass_judge(crop, ref)))
    assert _allows(83593, 2000, 10, judge, crop, ref)


def test_bare_verdict_line_is_denied(tmp_path: Path) -> None:
    """The old gameable escape hatch: a self-issued one-liner with no hash."""
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    time.sleep(0.05)
    judge = tmp_path / "x-judge.json"
    judge.write_text('{"verdict": "PASS", "by": "visual-debug-reviewer"}')
    assert not _allows(83593, 2000, 10, judge, crop)


def test_missing_rationale_is_denied(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    judge = tmp_path / "x-judge.json"
    d = _pass_judge(crop)
    d.pop("rationale")
    judge.write_text(json.dumps(d))
    assert not _allows(83593, 2000, 10, judge, crop)


def test_empty_rationale_is_denied(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    judge = tmp_path / "x-judge.json"
    d = _pass_judge(crop, rationale="   ")
    judge.write_text(json.dumps(d))
    assert not _allows(83593, 2000, 10, judge, crop)


def test_hash_mismatch_after_crop_rerender_is_denied(tmp_path: Path) -> None:
    """A verdict whose implSha256 was computed on a different crop (the crop was
    re-rendered after review) no longer applies — re-verified at read time,
    independent of mtime."""
    crop = tmp_path / "x.png"
    crop.write_bytes(b"original-crop")
    judge = tmp_path / "x-judge.json"
    judge.write_text(json.dumps(_pass_judge(crop)))
    # crop re-rendered to different bytes after the verdict was written
    crop.write_bytes(b"re-rendered-crop-different-bytes")
    assert not _allows(83593, 2000, 10, judge, crop)


def test_touch_newer_does_not_revive_mismatched_judge(tmp_path: Path) -> None:
    """mtime is forgeable; a newer judge file with a mismatched hash is still
    denied (the old guard would have allowed it on mtime alone)."""
    crop = tmp_path / "x.png"
    crop.write_bytes(b"crop-A")
    judge = tmp_path / "x-judge.json"
    judge.write_text(json.dumps(_pass_judge(crop)))
    crop.write_bytes(b"crop-B")  # changes hash
    time.sleep(1.1)
    judge.touch()  # judge now newer than crop, but hash no longer matches
    assert not _allows(83593, 2000, 10, judge, crop)


def test_above_cap_with_fail_judge_is_denied(tmp_path: Path) -> None:
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png")
    judge = tmp_path / "x-judge.json"
    d = _pass_judge(crop)
    d["verdict"] = "FAIL"
    judge.write_text(json.dumps(d))
    assert not _allows(83593, 2000, 10, judge, crop)


def test_dispatcher_short_hash_prefix_is_accepted(tmp_path: Path) -> None:
    """visual_judge_dispatcher records sha256[:12]; the cap accepts that prefix
    form so a verdict carrying the dispatcher's short hash validates."""
    crop = tmp_path / "x.png"
    crop.write_bytes(b"png-bytes-here")
    judge = tmp_path / "x-judge.json"
    d = _pass_judge(crop)
    d["implSha256"] = _sha256(crop)[:12]
    judge.write_text(json.dumps(d))
    assert _allows(83593, 2000, 10, judge, crop)


def test_section_compare_wires_cap_into_both_leniency_branches() -> None:
    sc = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    ).read_text(encoding="utf-8")
    assert "dssim-cap.sh" in sc, "section-compare must source the cap lib"
    assert sc.count("dssim_cap_allows") >= 2, (
        "cap must guard BOTH pass-by-dssim and pass-by-perceptual branches"
    )
    # P0·2: overrides counted + surfaced at closeout
    assert "JUDGE_OVERRIDE_COUNT" in sc
    assert "Visual-judge overrides" in sc
