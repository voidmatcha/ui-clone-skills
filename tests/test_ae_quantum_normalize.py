"""AE unit-normalization regression net (2026-07 blindness root cause).

ImageMagick 7.1.2-27 Q16 (brew, upgraded 2026-07-12) returns
`compare -metric AE` as pixel_count * QuantumRange (= count * 65535), NOT the
raw mismatched-pixel count every AE parser in this repo assumes. The 65535x
inflation pushed every ebpb section past the "saturated" severity band, so
section-compare's verdict became "19 saturated" with a dead gradient and the
iterator went blind.

lib/ae-quantum.sh detects the scale factor by BEHAVIOR (a synthetic 2x2
white/black compare has exactly 4 differing pixels) so it self-corrects if a
future IM build reverts to raw counts (divisor 1) or changes quantum depth. This
test is the tripwire for the NEXT time ImageMagick's metric behavior changes:
it runs the real binary on synthetic fixtures and asserts the NORMALIZED AE
equals the true pixel count, whatever the current IM build does.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "ae-quantum.sh"

_HAVE_MAGICK = shutil.which("magick") is not None or shutil.which("compare") is not None


def _norm(raw: str, w: str = "", h: str = "") -> str:
    """Run _ae_normalize from the helper under real bash and return its output."""
    script = f'source "{HELPER}"; _ae_normalize "{raw}" "{w}" "{h}"'
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=120
    )
    return out.stdout.strip()


def _divisor() -> str:
    out = subprocess.run(
        ["bash", "-c", f'source "{HELPER}"; _ae_quantum_divisor'],
        capture_output=True, text=True, timeout=120,
    )
    return out.stdout.strip()


def test_helper_exists() -> None:
    assert HELPER.is_file()


@pytest.mark.skipif(not _HAVE_MAGICK, reason="ImageMagick not installed")
def test_divisor_is_detected_and_positive() -> None:
    d = _divisor()
    assert d.isdigit() and int(d) >= 1, d


@pytest.mark.skipif(not _HAVE_MAGICK, reason="ImageMagick not installed")
def test_normalizes_inflated_ae_to_pixel_count() -> None:
    # A 100x100 all-different compare has 10000 mismatched pixels. Whatever the
    # current IM build reports (raw 10000, or 10000*65535 on Q16), the helper
    # must normalize it back to ~10000.
    d = int(_divisor())
    inflated = str(10000 * d)
    assert _norm(inflated, "100", "100") == "10000"


@pytest.mark.skipif(not _HAVE_MAGICK, reason="ImageMagick not installed")
def test_end_to_end_matches_pixel_count() -> None:
    # Run the REAL binary on a synthetic 100x100 white-vs-black pair and assert
    # the normalized AE equals the true pixel count (10000). This is the tripwire
    # for the next IM upgrade — if the divisor detection breaks, this fails loud.
    import tempfile

    magick = shutil.which("magick")
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w.png"
        b = Path(td) / "b.png"
        if magick:
            subprocess.run([magick, "-size", "100x100", "xc:white", str(w)], check=True)
            subprocess.run([magick, "-size", "100x100", "xc:black", str(b)], check=True)
            raw = subprocess.run(
                [magick, "compare", "-metric", "AE", str(w), str(b), "null:"],
                capture_output=True, text=True,
            ).stderr.strip().split()[0]
        else:
            conv = shutil.which("convert")
            comp = shutil.which("compare")
            assert conv and comp, "convert/compare must exist when magick is absent"
            subprocess.run([conv, "-size", "100x100", "xc:white", str(w)], check=True)
            subprocess.run([conv, "-size", "100x100", "xc:black", str(b)], check=True)
            raw = subprocess.run(
                [comp, "-metric", "AE", str(w), str(b), "null:"],
                capture_output=True, text=True,
            ).stderr.strip().split()[0]
        normalized = _norm(raw, "100", "100")
        assert normalized == "10000", f"raw={raw} normalized={normalized}"


@pytest.mark.skipif(not _HAVE_MAGICK, reason="ImageMagick not installed")
def test_zero_stays_zero() -> None:
    assert _norm("0", "100", "100") == "0"
