"""pass-by-dssim-strict: subpixel-phase artifact rescue (loop-ebpb-3 class).

A responsive (un-baked) impl rasterizes glyphs at a different subpixel phase
than a pixel-frozen ref crop: EVERY glyph pixel differs slightly, AE saturates
past both dssim_cap (10x threshold) and AE_SATURATION (800k), while dSSIM stays
~1e-6..0.02. loop-ebpb-3 measured 8 such rows at dSSIM <= 0.0212 whose dSSIM
was byte-continuous across the un-bake (footer 0.0000065 identical before and
after), while real defects measured 0.118 (nvti js-6) and 0.199 (N4 repro) —
a ~5x separation gap. SECTION_DSSIM_STRICT_MAX (default 0.03) sits inside it.

The tier is pure shell ladder logic in
skills/visual-debug/scripts/section-compare.sh. Following the repo pattern
(tests/test_section_compare_ae_floor.py), these tests exercise the exact awk
guard EXPRESSION in lockstep plus the structural shape of the tier: it must
keep every structural guard (measured ref variance, DOM severity, localized
band) while being exempt from dssim_cap_allows and the AE_SATURATION bound —
that exemption is the entire point of the tier.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "section-compare.sh"
)


def _strict_ceiling_passes(dssim: float, strict_max: float = 0.03) -> bool:
    # mirrors section-compare.sh:
    #   awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_STRICT_MAX" \
    #       'BEGIN{exit !(d+0 <= max+0)}'
    return (
        subprocess.run(
            [
                "awk",
                "-v",
                f"d={dssim}",
                "-v",
                f"max={strict_max}",
                "BEGIN{exit !(d+0 <= max+0)}",
            ]
        ).returncode
        == 0
    )


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _strict_tier_block(body: str) -> str:
    """The elif block that assigns SEV="pass-by-dssim-strict": from its opening
    `elif` back-scanned from the assignment, through the assignment line."""
    idx = body.index('SEV="pass-by-dssim-strict"')
    start = body.rindex("elif", 0, idx)
    block = body[start : idx + len('SEV="pass-by-dssim-strict"')]
    # Strip comment lines: the guards under test are CODE conditions; the
    # tier's own comment legitimately names the mechanisms it is exempt from.
    return "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
    )


# ── behavior: the strict ceiling separates the artifact class from real defects ──


def test_strict_ceiling_rescues_campaign_artifact_rows() -> None:
    # loop-ebpb-3 measured artifact band: footer 0.0000065 .. OqsER-2 0.0212.
    assert _strict_ceiling_passes(0.0000065) is True
    assert _strict_ceiling_passes(0.0212) is True


def test_strict_ceiling_rejects_real_defects() -> None:
    # nvti js-6 inner-DOM wall (0.118) and the N4 distributed defect (0.199)
    # must NOT ride the strict tier.
    assert _strict_ceiling_passes(0.118) is False
    assert _strict_ceiling_passes(0.199) is False


def test_strict_ceiling_boundary() -> None:
    assert _strict_ceiling_passes(0.03) is True
    assert _strict_ceiling_passes(0.0301) is False


# ── structure: lockstep with the script body ──


def test_strict_max_default_is_0_03() -> None:
    assert 'SECTION_DSSIM_STRICT_MAX="${SECTION_DSSIM_STRICT_MAX:-0.03}"' in _body()


def test_strict_tier_exists_and_keeps_structural_guards() -> None:
    block = _strict_tier_block(_body())
    assert '[ "$REF_HAS_VARIANCE" = "1" ]' in block
    assert "SECTION_DSSIM_STRICT_MAX" in block
    assert '_perceptual_dom_sev "$NAME"' in block
    assert "critical" in block and "major" in block
    assert '! _perceptual_localized_defect "$REF_IMG" "$IMPL_IMG"' in block


def test_strict_tier_requires_refshot_clean_anti_cheat() -> None:
    # Screenshot-paste cheat: an impl that EMBEDS the ref screenshot scores
    # dSSIM~0 and satisfies every other strict-tier condition (variance
    # present, no DOM delta, no localized band). PERCEPTUAL_REFSHOT_CLEAN is
    # the detector's veto on pass-by-perceptual; the strict tier must carry
    # the SAME veto or it becomes the cheat's new front door (fable MAJOR-1).
    block = _strict_tier_block(_body())
    assert '[ "$PERCEPTUAL_REFSHOT_CLEAN" = "1" ]' in block


def test_strict_tier_requires_dense_mode() -> None:
    # In SECTION_PERCEPTUAL_DENSE=0 escape-hatch mode structure-severity.txt
    # and the refshot check are never produced: _perceptual_dom_sev would
    # default to "ok" (vacuous guard) and the documented byte-identical
    # strict-AE contract would break. The tier must be gated on DENSE=1
    # (fable MAJOR-2).
    block = _strict_tier_block(_body())
    assert '[ "$SECTION_PERCEPTUAL_DENSE" = "1" ]' in block


def test_strict_tier_is_exempt_from_cap_and_saturation() -> None:
    # The exemption IS the fix: a subpixel-phase row saturates AE, so any
    # dssim_cap_allows or SATURATION condition in this tier would keep the
    # loop-ebpb-3 rows failing exactly as before.
    block = _strict_tier_block(_body())
    assert "dssim_cap_allows" not in block
    assert "SATURATION" not in block


def test_dssim_computed_even_when_ae_saturated() -> None:
    # The DSSIM_SCORE computation gate must no longer carry an upper
    # AE_PER_MPX bound: saturated rows need a dSSIM measurement for the strict
    # tier to evaluate at all (pre-fix: `-lt "$SATURATION"` suppressed it).
    body = _body()
    m = re.search(
        r'if \[ "\$DSSIM_FALLBACK" = "1" \].*?DSSIM_SCORE=\$\(dssim',
        body,
        re.S,
    )
    assert m is not None, "DSSIM_SCORE computation gate not found"
    assert "SATURATION" not in m.group(0)


def test_loose_tiers_keep_their_bounds() -> None:
    # Anti-gaming invariant: pass-by-dssim and pass-by-perceptual must STILL
    # carry dssim_cap_allows + < SATURATION — the strict tier must not have
    # loosened them.
    body = _body()
    for sev in ('SEV="pass-by-dssim"', 'SEV="pass-by-perceptual"'):
        idx = body.index(sev)
        start = body.rindex("elif", 0, idx)
        block = body[start:idx]
        assert "dssim_cap_allows" in block, sev
        assert '-lt "$SATURATION"' in block, sev
