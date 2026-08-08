"""Folded-D compare entrypoint: a thin reuse wrapper over the pure metrics.

Usage:
    python -m ui_clone.compare <ref_image> <impl_image> [--json] [--fuzz 8%]

This module does NOT reinvent any metric. It wraps the existing pure
comparison functions and prints a compact verdict:

  - multiscale dssim   ← 1 - ``ui_clone.metrics.multiscale_ssim`` (scikit-image)
  - AE / AE per Mpx    ← ImageMagick ``compare -metric AE`` (degrades gracefully
                          to ``null`` + a note when ImageMagick is unavailable)
  - ΔE2000 (mean)      ← ``ui_clone.metrics.mean_delta_e2000`` (scikit-image):
                          a perceptual color-drift signal orthogonal to AE. A
                          uniform tint/gamma shift the fuzz-tolerant AE counts as
                          identical still raises severity ok→minor here.
  - defect severity    ← ``ui_clone.metrics.defect_severity`` (section-level
                          classification reused verbatim)

Exit codes:
    0  comparison ran (verdict printed)
    1  runtime error while comparing (e.g. unreadable image)
    2  usage error (missing argument / missing file)

The verdict's ``severity`` is derived from the AE value when ImageMagick is
present. When AE is unavailable, severity stays CONSERVATIVE: a clearly
divergent dssim still surfaces a defect, but a low dssim degrades to
``unmeasured`` (never ``ok``) — a clean pass requires the AE metric the
dssim pass paths are gated on, matching section-compare semantics.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # noqa: S404 - fixed-arg ImageMagick invocation, no shell
import sys
import tempfile
from pathlib import Path
from typing import Any

from ui_clone.metrics import (
    JND_DELTA_E2000,
    defect_severity,
    mean_delta_e2000,
    multiscale_ssim,
)

# Default fuzz tolerance for AE, matching section-compare.sh's SECTION_FUZZ
# default: pixels whose color diff is within this percentage are treated as
# identical (filters sub-pixel AA / font-hinting / JPEG grain noise).
DEFAULT_FUZZ = "8%"

# AE/Mpx threshold reused as the severity cutoff. defect_severity()'s own
# default threshold is 2000 AE; we pass AE/Mpx so the section-level bands apply
# on a resolution-normalized basis.
_AE_SEVERITY_THRESHOLD = 2000


def _image_megapixels(path: Path) -> float | None:
    """Return the image area in megapixels, or None if it cannot be read."""
    try:
        from PIL import Image  # local import keeps module import cheap

        with Image.open(path) as img:
            w, h = img.size
    except Exception:  # noqa: BLE001 - any read failure → unknown Mpx
        return None
    if w <= 0 or h <= 0:
        return None
    return (w * h) / 1_000_000.0


def _imagemagick_compare() -> list[str] | None:
    """Return the ImageMagick ``compare`` command prefix, or None if absent.

    Supports both the modern ``magick compare ...`` and the legacy standalone
    ``compare`` binary.
    """
    if shutil.which("magick"):
        return ["magick", "compare"]
    if shutil.which("compare"):
        return ["compare"]
    return None


_AE_QUANTUM_DIVISOR: int | None = None


def _ae_quantum_divisor() -> int:
    """Detect the ImageMagick AE scale factor by behavior, cached per process.

    ImageMagick 7.1.2-27 Q16 (brew, 2026-07-12) returns ``compare -metric AE``
    as pixel_count * QuantumRange (= count * 65535), NOT the raw pixel count.
    A synthetic 2x2 white/black compare has exactly 4 differing pixels, so the
    divisor is round(reported / 4). Self-corrects if a future build reverts to
    raw counts (divisor 1) or changes quantum depth. Mirrors the shell
    lib/ae-quantum.sh so the Python and bash AE paths agree.
    """
    global _AE_QUANTUM_DIVISOR
    if _AE_QUANTUM_DIVISOR is not None:
        return _AE_QUANTUM_DIVISOR
    prefix = _imagemagick_compare()
    if prefix is None:
        _AE_QUANTUM_DIVISOR = 1
        return 1
    conv = "magick" if shutil.which("magick") else "convert"
    with (
        tempfile.NamedTemporaryFile(suffix=".png") as w,
        tempfile.NamedTemporaryFile(suffix=".png") as b,
    ):
        try:
            subprocess.run([conv, "-size", "2x2", "xc:white", w.name], check=True, timeout=30)
            subprocess.run([conv, "-size", "2x2", "xc:black", b.name], check=True, timeout=30)
            proc = subprocess.run(
                [*prefix, "-metric", "AE", w.name, b.name, "null:"],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            _AE_QUANTUM_DIVISOR = 1
            return 1
    tok = (proc.stderr or proc.stdout or "").strip().split()
    try:
        reported = float(tok[0]) if tok else 0.0
    except ValueError:
        reported = 0.0
    _AE_QUANTUM_DIVISOR = max(1, round(reported / 4)) if reported > 4 else 1
    return _AE_QUANTUM_DIVISOR


def absolute_error(ref: Path, impl: Path, *, fuzz: str = DEFAULT_FUZZ) -> int | None:
    """Pixel-count absolute error via ImageMagick ``compare -metric AE``.

    Returns the AE pixel count, or None when ImageMagick is unavailable or the
    comparison fails (graceful degradation — the caller skips the metric).
    """
    cmd_prefix = _imagemagick_compare()
    if cmd_prefix is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as diff:
        cmd = [
            *cmd_prefix,
            "-metric",
            "AE",
            "-fuzz",
            fuzz,
            # Resolve to absolute paths so a filename beginning with '-' cannot
            # be parsed by ImageMagick as an option.
            str(ref.resolve()),
            str(impl.resolve()),
            diff.name,
        ]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed args, no shell, no user injection
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    # ImageMagick writes the metric to stderr; it exits non-zero when images
    # differ, which is expected — parse the numeric token regardless of code.
    raw = (proc.stderr or proc.stdout or "").strip().split()
    if not raw:
        return None
    token = raw[0]
    try:
        # Normalize count*QuantumRange (IM Q16) back to a raw pixel count.
        return round(float(token) / _ae_quantum_divisor())
    except ValueError:
        return None


def compare_images(ref: Path, impl: Path, *, fuzz: str = DEFAULT_FUZZ) -> dict[str, Any]:
    """Compare two images, reusing the existing pure metric functions.

    Returns a JSON-serializable verdict dict. AE-derived fields are ``None``
    with a ``notes`` entry when ImageMagick is unavailable.
    """
    notes: list[str] = []

    # --- structural: multiscale dssim (reuse metrics.multiscale_ssim) ---
    ssim = multiscale_ssim(ref, impl)
    dssim = 1.0 - ssim

    # --- perceptual: mean CIEDE2000 color drift (reuse metrics.mean_delta_e2000) ---
    # Orthogonal to AE: catches a uniform tint/gamma shift that the fuzz-tolerant
    # pixel AE counts as identical. Degrades to None on any read failure.
    try:
        delta_e = mean_delta_e2000(ref, impl)
    except (ValueError, OSError):
        delta_e = None

    # --- pixel: AE / AE per Mpx (ImageMagick, graceful degradation) ---
    ae = absolute_error(ref, impl, fuzz=fuzz)
    ae_per_mpx: float | None = None
    if ae is None:
        notes.append("AE unavailable: ImageMagick `compare` not found or failed; AE metric skipped")
    else:
        mpx = _image_megapixels(ref)
        if mpx and mpx > 0:
            ae_per_mpx = ae / mpx
        else:
            notes.append("AE/Mpx unavailable: could not read reference image dimensions")

    # --- severity: reuse metrics.defect_severity (AE-driven when present) ---
    if ae_per_mpx is not None:
        severity = defect_severity(
            ae=round(ae_per_mpx), threshold=_AE_SEVERITY_THRESHOLD
        )
    elif ae is not None:
        severity = defect_severity(ae=ae, threshold=_AE_SEVERITY_THRESHOLD)
    else:
        # No AE (ImageMagick unavailable). dssim ALONE cannot certify a pass:
        # section-compare gates every dssim pass behind AE/Mpx, ref-variance, and
        # localized-defect checks (dssim is degenerate on low-variance crops), so
        # treating a low dssim as "ok" here would be a false pass. A clearly
        # divergent dssim still surfaces the defect; anything below that degrades
        # to "unmeasured" — never "ok".
        if dssim > 0.10:
            severity = "critical"
        elif dssim > 0.03:
            severity = "major"
        else:
            severity = "unmeasured"
        notes.append(
            "AE unavailable — severity is dssim-only and conservative: a low "
            "dssim is reported 'unmeasured' (not a pass), since a clean verdict "
            "requires the AE metric the dssim pass paths are gated on"
        )

    # --- perceptual gate: a sub-fuzz tint/gamma drift is still a real defect ---
    # AE within the fuzz band can certify "ok" while the whole crop is uniformly
    # off-color. When the mean ΔE2000 exceeds the just-noticeable difference,
    # surface it: an otherwise-clean verdict can be at most "minor" (never a
    # silent pass), so the perceptual miss the pixel AE hid becomes visible.
    if delta_e is not None and delta_e > JND_DELTA_E2000 and severity == "ok":
        severity = "minor"
        notes.append(
            f"perceptual color drift ΔE2000={delta_e:.1f} exceeds JND "
            f"({JND_DELTA_E2000}): a sub-fuzz tint/gamma miss the pixel AE does "
            "not surface — severity raised ok→minor"
        )

    return {
        "ref": str(ref),
        "impl": str(impl),
        "dssim": round(dssim, 6),
        "ssim": round(ssim, 6),
        "ae": ae,
        "ae_per_mpx": round(ae_per_mpx, 1) if ae_per_mpx is not None else None,
        "delta_e2000": round(delta_e, 2) if delta_e is not None else None,
        "severity": severity,
        "notes": notes,
    }


def _format_text(verdict: dict[str, Any]) -> str:
    """Render a compact one-block text verdict."""
    ae = verdict["ae"]
    ae_per_mpx = verdict["ae_per_mpx"]
    ae_line = (
        f"AE={ae} (AE/Mpx={ae_per_mpx})"
        if ae is not None and ae_per_mpx is not None
        else (f"AE={ae}" if ae is not None else "AE=n/a (ImageMagick unavailable)")
    )
    delta_e = verdict.get("delta_e2000")
    lines = [
        f"ref:      {verdict['ref']}",
        f"impl:     {verdict['impl']}",
        f"dssim:    {verdict['dssim']} (multiscale)",
        f"{ae_line}",
        f"ΔE2000:   {delta_e if delta_e is not None else 'n/a'} (mean, perceptual)",
        f"severity: {verdict['severity']}",
    ]
    for note in verdict["notes"]:
        lines.append(f"note:     {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: compare two images, print a JSON or text verdict.

    Returns the process exit code (0 ok, 1 runtime error, 2 usage/missing file).
    """
    parser = argparse.ArgumentParser(
        prog="python -m ui_clone.compare",
        description="Compare two images (multiscale dssim + AE + defect severity).",
    )
    parser.add_argument("ref_image", help="reference image path")
    parser.add_argument("impl_image", help="implementation image path")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--fuzz",
        default=DEFAULT_FUZZ,
        # Escape '%' so argparse does not treat it as a format specifier.
        help="ImageMagick AE fuzz tolerance (default: "
        + DEFAULT_FUZZ.replace("%", "%%"),
    )
    args = parser.parse_args(argv)

    ref = Path(args.ref_image)
    impl = Path(args.impl_image)
    for label, p in (("reference", ref), ("implementation", impl)):
        if not p.is_file():
            print(f"error: {label} image not found: {p}", file=sys.stderr)
            return 2

    try:
        verdict = compare_images(ref, impl, fuzz=args.fuzz)
    except (ValueError, OSError) as e:
        print(f"error: comparison failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print(_format_text(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
