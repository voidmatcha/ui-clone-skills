"""Tests for the folded-D compare entrypoint (`python -m ui_clone.compare`).

Hermetic: no network, no real site. Test images are generated in tmp_path via
PIL/numpy; the whole module is skipped when PIL is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="PIL required to synthesize test images")
pytest.importorskip("numpy", reason="numpy required to synthesize test images")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from ui_clone import compare  # noqa: E402


def _write_image(path: Path, arr: np.ndarray) -> Path:
    """Write a uint8 RGB array to a PNG and return the path."""
    Image.fromarray(arr.astype(np.uint8), mode="RGB").save(path)
    return path


@pytest.fixture
def identical_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Two byte-identical small images (low divergence expected)."""
    rng = np.random.default_rng(1234)
    arr = rng.integers(0, 256, size=(32, 48, 3), dtype=np.uint8)
    ref = _write_image(tmp_path / "ref.png", arr)
    impl = _write_image(tmp_path / "impl.png", arr.copy())
    return ref, impl


def test_module_importable() -> None:
    """The wrapper exposes a callable main() and compare_images()."""
    assert callable(compare.main)
    assert callable(compare.compare_images)


def test_main_callable_identical_low_divergence(
    identical_pair: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """main() over two identical images returns 0 and a low-divergence verdict."""
    ref, impl = identical_pair
    rc = compare.main([str(ref), str(impl)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dssim:" in out
    # With ImageMagick present, AE=0 → "ok"; without it, a clean pass can't be
    # certified from dssim alone → "unmeasured". Never a false defect here.
    assert "severity: ok" in out or "severity: unmeasured" in out


def test_compare_images_identical_is_zero_dssim(
    identical_pair: tuple[Path, Path],
) -> None:
    """Identical images → dssim ~0 and severity ok."""
    ref, impl = identical_pair
    verdict = compare.compare_images(ref, impl)
    assert verdict["dssim"] == pytest.approx(0.0, abs=1e-6)
    # AE present → "ok"; AE absent → conservative "unmeasured" (never a false ok).
    assert verdict["severity"] in ("ok", "unmeasured")
    # When ImageMagick is present AE should be 0; when absent it degrades to None.
    assert verdict["ae"] in (0, None)


def test_json_emits_valid_json(
    identical_pair: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """--json emits parseable JSON with the documented fields."""
    ref, impl = identical_pair
    rc = compare.main([str(ref), str(impl), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    for key in ("ref", "impl", "dssim", "ssim", "ae", "ae_per_mpx", "severity", "notes"):
        assert key in payload
    assert payload["dssim"] == pytest.approx(0.0, abs=1e-6)


def test_divergent_pair_higher_dssim(tmp_path: Path) -> None:
    """A clearly different image yields higher dssim than an identical one."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 256, size=(32, 48, 3), dtype=np.uint8)
    ref = _write_image(tmp_path / "ref.png", base)
    # Invert the bottom half — a large structural change.
    other = base.copy()
    other[16:, :, :] = 255 - other[16:, :, :]
    impl = _write_image(tmp_path / "impl.png", other)
    verdict = compare.compare_images(ref, impl)
    assert verdict["dssim"] > 0.01


def test_missing_file_errors_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing input exits non-zero with a clean message, no traceback."""
    real = _write_image(
        tmp_path / "ref.png", np.zeros((8, 8, 3), dtype=np.uint8)
    )
    rc = compare.main([str(real), str(tmp_path / "does_not_exist.png")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert "Traceback" not in captured.err


def test_missing_file_subprocess_no_traceback(tmp_path: Path) -> None:
    """End-to-end `python -m ui_clone.compare` on a missing file: rc=2, clean."""
    real = _write_image(
        tmp_path / "ref.png", np.zeros((8, 8, 3), dtype=np.uint8)
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ui_clone.compare",
            str(real),
            str(tmp_path / "missing.png"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "not found" in proc.stderr


def test_absolute_error_graceful_without_imagemagick(
    identical_pair: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ImageMagick is absent, absolute_error returns None and the verdict
    degrades gracefully (no crash, dssim-only severity, explanatory note)."""
    ref, impl = identical_pair
    monkeypatch.setattr(compare, "_imagemagick_compare", lambda: None)
    assert compare.absolute_error(ref, impl) is None
    verdict = compare.compare_images(ref, impl)
    assert verdict["ae"] is None
    assert verdict["ae_per_mpx"] is None
    # Conservative: identical images with NO AE cannot be certified "ok" — a
    # clean pass requires the AE metric the dssim pass paths are gated on.
    assert verdict["severity"] == "unmeasured"
    assert any("AE unavailable" in n for n in verdict["notes"])


def test_delta_e2000_reported_and_zero_for_identical(
    identical_pair: tuple[Path, Path],
) -> None:
    """Identical images → mean ΔE2000 ~0 and the field is present in the verdict."""
    ref, impl = identical_pair
    verdict = compare.compare_images(ref, impl)
    assert verdict["delta_e2000"] == pytest.approx(0.0, abs=1e-3)


def test_uniform_tint_drift_escalates_ok_to_minor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uniform tint shift the fuzz-tolerant pixel AE calls 'identical' must NOT
    pass: the perceptual ΔE2000 gate raises severity ok→minor and records a note.

    AE is forced to 0 to simulate the fuzz band swallowing the sub-fuzz tint, so
    the ONLY reason severity leaves 'ok' is the perceptual gate under test."""
    base = np.full((48, 64, 3), 128, dtype=np.uint8)
    ref = _write_image(tmp_path / "ref.png", base)
    tinted = base.copy()
    tinted[:, :, 0] = 170  # nudge the red channel uniformly (above the ΔE JND)
    impl = _write_image(tmp_path / "impl.png", tinted)

    # Fuzz swallows the pixel diff → AE=0 → the AE path certifies "ok".
    monkeypatch.setattr(compare, "absolute_error", lambda *a, **k: 0)

    verdict = compare.compare_images(ref, impl)
    assert verdict["ae"] == 0
    assert verdict["delta_e2000"] is not None
    assert verdict["delta_e2000"] > compare.JND_DELTA_E2000
    # A uniform tint is not structural, so the AE path called this "ok"; the
    # perceptual gate must surface it as at least "minor" (never a silent pass).
    assert verdict["severity"] == "minor"
    assert any("ΔE2000" in n for n in verdict["notes"])


def test_no_imagemagick_divergent_is_never_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ImageMagick + a clearly divergent pair: severity surfaces the defect
    (critical/major) and is NEVER 'ok' — the false-pass Codex flagged."""
    rng = np.random.default_rng(99)
    base = rng.integers(0, 256, size=(48, 64, 3), dtype=np.uint8)
    ref = _write_image(tmp_path / "ref.png", base)
    other = base.copy()
    other[:, :, :] = 255 - other[:, :, :]  # full inversion → large dssim
    impl = _write_image(tmp_path / "impl.png", other)
    monkeypatch.setattr(compare, "_imagemagick_compare", lambda: None)
    verdict = compare.compare_images(ref, impl)
    assert verdict["ae"] is None
    assert verdict["severity"] != "ok"
    assert verdict["severity"] in ("critical", "major", "unmeasured")
