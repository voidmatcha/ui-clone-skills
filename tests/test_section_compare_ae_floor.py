"""N4 regression: the pass-by-ae-floor tier must carry the global-dssim ceiling
AND a ref-area bound its sibling pass tiers have, so a low-absolute-AE but
high-global-dssim DISTRIBUTED defect cannot ride the absolute-AE floor to green.

The tier is pure shell ladder logic in skills/visual-debug/scripts/section-compare.sh
(the `SEV="pass-by-ae-floor"` elif). A full AE-crop integration run needs the
section-compare harness + finicky AE/dssim image tuning; this instead exercises the
exact awk guard EXPRESSIONS used in that elif, kept in lockstep with the script. It
encodes the reproduced hole (AE=4988 / dssim=0.199 must be REJECTED) and the floor's
legitimate navercorp-header purpose (a tiny low-dssim crop must still be rescued).
"""
from __future__ import annotations

import subprocess


def _dssim_ceiling_passes(dssim: float, dense_max: float = 0.12) -> bool:
    # mirrors section-compare.sh:
    #   awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_DENSE_MAX" 'BEGIN{exit !(d+0 <= max+0)}'
    return subprocess.run(
        ["awk", "-v", f"d={dssim}", "-v", f"max={dense_max}", "BEGIN{exit !(d+0 <= max+0)}"]
    ).returncode == 0


def _area_bound_passes(w: int, h: int, max_mpx: float = 0.5) -> bool:
    # mirrors section-compare.sh:
    #   awk -v w="$REF_W" -v h="$REF_H" -v max="${SECTION_AE_FLOOR_MAX_MPX:-0.5}" \
    #       'BEGIN{ area=(w*h)/1000000; exit !(area > 0 && area <= max+0) }'
    return subprocess.run(
        ["awk", "-v", f"w={w}", "-v", f"h={h}", "-v", f"max={max_mpx}",
         "BEGIN{ area=(w*h)/1000000; exit !(area > 0 && area <= max+0) }"]
    ).returncode == 0


def test_ae_floor_rejects_distributed_high_dssim_defect() -> None:
    # The reproduced N4 hole: AE=4988 (< 8000 floor) but dssim=0.199 (> 0.12).
    # The global-dssim ceiling must REJECT it so the section is NOT rescued.
    assert _dssim_ceiling_passes(0.199) is False


def test_ae_floor_preserves_navercorp_header_artifact() -> None:
    # The floor's legitimate purpose: a tiny ~0.06 Mpx header with a near-zero
    # ABSOLUTE diff and low dssim must still be rescued (both guards pass).
    assert _dssim_ceiling_passes(0.05) is True
    assert _area_bound_passes(1440, 42) is True  # 0.06 Mpx


def test_ae_floor_rejects_full_size_section() -> None:
    # A full-size section with few absolute diff pixels is low defect DENSITY, not
    # a denominator artifact — the area bound must keep the floor off it.
    assert _area_bound_passes(1920, 1080) is False  # ~2.07 Mpx
    assert _area_bound_passes(800, 600) is True       # 0.48 Mpx (under 0.5)


def test_ae_floor_dssim_ceiling_boundary() -> None:
    assert _dssim_ceiling_passes(0.12) is True    # at the ceiling
    assert _dssim_ceiling_passes(0.1201) is False  # just over
