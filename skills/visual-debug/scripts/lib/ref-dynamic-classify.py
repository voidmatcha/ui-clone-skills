#!/usr/bin/env python3
"""Classify ref-dynamic sections from the reference's OWN frame-to-frame variance.

Reads sections/ref/<name>.png and sections/ref-calib/<name>.png (two independent
reference page loads) and, per section, measures their AE/Mpx + dssim divergence.
A section the reference cannot self-match (AE/Mpx > threshold) is REF-PROVEN
DYNAMIC — framer scroll-scrub, splash, carousel — and section-compare switches it
to structural/layout parity. Writes sections/ref-dynamic.json.

Detection-preserving: classification is driven ONLY by the reference's instability
(measured here, impl never involved); the parity verdict + its noise floor live in
ui_clone.section_dynamic and are unit-tested (tests/measure/test_section_dynamic.py).

Usage: ref-dynamic-classify.py <sections-dir>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from ui_clone.section_dynamic import is_ref_dynamic  # noqa: E402


def _identify(img: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            ["magick", "identify", "-format", "%w %h", str(img)],
            capture_output=True, text=True, check=False, timeout=30,
        ).stdout.strip().split()
        return int(out[0]), int(out[1])
    except (ValueError, IndexError, OSError, subprocess.SubprocessError):
        return 0, 0


def _ae(ref: Path, calib: Path, fuzz: str) -> float | None:
    """AbsoluteError pixel count between the two reference frames (calib resized
    to ref dims so a 1px box drift never inflates it)."""
    rw, rh = _identify(ref)
    if rw <= 0 or rh <= 0:
        return None
    try:
        resized = ref.parent / f".{ref.stem}.calibresize.png"
        subprocess.run(
            ["magick", str(calib), "-resize", f"{rw}x{rh}!", "-quality", "95", str(resized)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        proc = subprocess.run(
            ["magick", "compare", "-metric", "AE", "-fuzz", fuzz,
             str(ref), str(resized), "null:"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        resized.unlink(missing_ok=True)
        tok = (proc.stderr or proc.stdout).strip().split()[0]
        return float(tok)
    except (ValueError, IndexError, OSError, subprocess.SubprocessError):
        return None


def _dssim(ref: Path, calib: Path) -> float | None:
    # Resize calib to ref dims first — the calib crop is captured at its native
    # impl-path box (a scrub element may be w964 vs the ref's w1440), and dssim
    # requires matching dimensions. The box-scale divergence is scored separately
    # by the layout-box dim check; here we measure CONTENT divergence.
    rw, rh = _identify(ref)
    if rw <= 0 or rh <= 0:
        return None
    try:
        resized = ref.parent / f".{ref.stem}.calibdssim.png"
        subprocess.run(
            ["magick", str(calib), "-resize", f"{rw}x{rh}!", "-quality", "95", str(resized)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        out = subprocess.run(
            ["dssim", str(ref), str(resized)],
            capture_output=True, text=True, check=False, timeout=60,
        ).stdout.strip().split()
        resized.unlink(missing_ok=True)
        return float(out[0]) if out else None
    except (ValueError, IndexError, OSError, subprocess.SubprocessError):
        return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: ref-dynamic-classify.py <sections-dir>", file=sys.stderr)
        return 2
    sections = Path(argv[0])
    ref_dir, calib_dir = sections / "ref", sections / "ref-calib"
    if not ref_dir.is_dir() or not calib_dir.is_dir():
        return 0
    threshold = float(os.environ.get("SECTION_DYNAMIC_THRESHOLD", "2000"))
    fuzz = os.environ.get("SECTION_FUZZ", "8%")

    out: dict[str, dict] = {}
    for ref_img in sorted(ref_dir.glob("*.png")):
        name = ref_img.stem
        calib_img = calib_dir / f"{name}.png"
        if not calib_img.is_file():
            continue
        rw, rh = _identify(ref_img)
        ae = _ae(ref_img, calib_img, fuzz)
        if ae is None or rw <= 0 or rh <= 0:
            continue
        area_mpx = (rw * rh) / 1_000_000.0
        self_ae_per_mpx = (ae / area_mpx) if area_mpx > 0 else 0.0
        dynamic = is_ref_dynamic(self_ae_per_mpx, threshold=threshold)
        rec = {
            "selfAePerMpx": round(self_ae_per_mpx, 1),
            "refW": rw,
            "refH": rh,
            "dynamic": dynamic,
        }
        if dynamic:
            rec["selfDssim"] = _dssim(ref_img, calib_img)
            # Calib crop box: the reference's OWN cross-load box dims. The
            # structural-parity dim check allows the impl to diverge up to this
            # ref-self variance (scroll-scrub / scaffold-scale sections re-measure
            # their bounding box per load), so a scrub box shift is not a defect
            # but a genuinely resized impl box still is.
            cw, ch = _identify(calib_img)
            rec["calibW"] = cw
            rec["calibH"] = ch
        out[name] = rec

    (sections / "ref-dynamic.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )
    dyn = sorted(n for n, r in out.items() if r.get("dynamic"))
    print(f"▸ ref-dynamic: {len(dyn)}/{len(out)} section(s) ref-proven dynamic: {' '.join(dyn) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
