from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "font-binaries-present-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )


def _art(ref: Path) -> dict:
    data: dict = json.loads((ref / "font-binaries-present.json").read_text())
    return data


def _report(
    ref: Path,
    impl: Path,
    totals: dict,
    transferred: Sequence[dict] = (),
    skipped: Sequence[dict] = (),
    missing: Sequence[dict] = (),
) -> None:
    (ref / "font-transfer.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "implPublicDir": str(impl / "public"),
        "totals": totals,
        "transferred": list(transferred),
        "skipped": list(skipped),
        "missing": list(missing),
    }))


def test_referenced_but_no_binaries_present_fails(tmp_path: Path) -> None:
    """Fonts referenced by the ref CSS but nothing delivered to impl/public →
    fonts 404 to system fallbacks → FAIL (the navercorp class)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "public").mkdir(parents=True)
    _report(
        ref, impl,
        totals={"referenced": 3, "transferred": 0, "missing": 3, "skipped": 0},
        missing=[{"urlPath": "/font/a.woff2", "basename": "a.woff2", "reason": "gap"}],
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["presentOnDisk"] == 0


def test_report_claims_transfer_but_file_absent_fails(tmp_path: Path) -> None:
    """Belt-and-suspenders: the report says a font transferred, but the binary is
    not on disk under impl/public → still FAIL."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "public").mkdir(parents=True)
    _report(
        ref, impl,
        totals={"referenced": 1, "transferred": 1, "missing": 0, "skipped": 0},
        transferred=[{"urlPath": "/font/ghost.woff2", "basename": "ghost.woff2"}],
    )
    proc = _run(ref, impl)
    assert proc.returncode == 1
    assert _art(ref)["status"] == "fail"


def test_binary_present_on_disk_passes(tmp_path: Path) -> None:
    """A referenced font whose binary is on disk under impl/public → PASS, even
    when some other referenced font is still missing (advisory extraction gap)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    font_dir = impl / "public" / "font"
    font_dir.mkdir(parents=True)
    (font_dir / "real.woff2").write_bytes(b"x")
    _report(
        ref, impl,
        totals={"referenced": 2, "transferred": 1, "missing": 1, "skipped": 0},
        transferred=[{"urlPath": "/font/real.woff2", "basename": "real.woff2"}],
        missing=[{"urlPath": "/font/x.woff2", "basename": "x.woff2", "reason": "gap"}],
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["presentOnDisk"] == 1


def test_same_font_basename_in_rewritten_public_subdirectory_passes(
    tmp_path: Path,
) -> None:
    """CSS may rewrite the original root-relative URL while retaining the same
    font binary under another public subdirectory."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    rewritten_dir = impl / "public" / "fonts"
    rewritten_dir.mkdir(parents=True)
    (rewritten_dir / "market-sans.woff2").write_bytes(b"x")
    _report(
        ref, impl,
        totals={"referenced": 1, "transferred": 1, "missing": 0, "skipped": 0},
        transferred=[
            {
                "urlPath": "/original/font/market-sans.woff2",
                "basename": "market-sans.woff2",
            },
        ],
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["presentOnDisk"] == 1


def test_unrelated_font_binary_does_not_satisfy_reported_basename(
    tmp_path: Path,
) -> None:
    """Recursive fallback remains fail-closed: another font is not evidence
    that the reported binary was delivered."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    font_dir = impl / "public" / "fonts"
    font_dir.mkdir(parents=True)
    (font_dir / "unrelated.woff2").write_bytes(b"x")
    _report(
        ref, impl,
        totals={"referenced": 1, "transferred": 1, "missing": 0, "skipped": 0},
        transferred=[
            {
                "urlPath": "/original/font/expected.woff2",
                "basename": "expected.woff2",
            },
        ],
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["presentOnDisk"] == 0


def test_already_present_skip_counts_as_delivered(tmp_path: Path) -> None:
    """Idempotent transfer: fonts already in impl/public land in skipped[] with
    transferred==0 — must NOT fail, because the binaries are present."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    font_dir = impl / "public" / "font"
    font_dir.mkdir(parents=True)
    (font_dir / "kept.woff2").write_bytes(b"x")
    _report(
        ref, impl,
        totals={"referenced": 1, "transferred": 0, "missing": 0, "skipped": 1},
        skipped=[{"urlPath": "/font/kept.woff2", "reason": "already-present-in-public"}],
    )
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _art(ref)["status"] == "pass"


def test_no_report_passes(tmp_path: Path) -> None:
    """No font-transfer.json (transfer step not run) → PASS, nothing to verify."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "public").mkdir(parents=True)
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _art(ref)["status"] == "pass"


def test_zero_referenced_passes(tmp_path: Path) -> None:
    """referenced == 0 (only CDN/absolute fonts) → PASS."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "public").mkdir(parents=True)
    _report(ref, impl, totals={"referenced": 0, "transferred": 0, "missing": 0, "skipped": 0})
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _art(ref)["status"] == "pass"
