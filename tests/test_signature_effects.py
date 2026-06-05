from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "extract"))

import _signature_effects as sigfx  # noqa: E402


def test_detects_per_character_scroll_scrub() -> None:
    """The framer-motion per-character scroll reveal (totalChars +
    scrollYProgress + per-char + color) must be detected as a candidate."""
    bundle = (
        "{let{char:t,index:a,totalChars:r,scrollYProgress:i,isMobile:l,prevChar:o}=e,"
        "d=(l?.15:.2)+a/r*(l?.18:.12);return color:mix(d)}"
    )
    cands = sigfx.extract_candidates([bundle])
    assert len(cands) == 1, cands
    c = cands[0]
    assert c["effectType"] == "per-character-scroll-scrub"
    assert c["confidence"] == "high"
    assert c["library"] == "framer-motion"
    assert c["selector"] is None and c["selectorConfidence"] == "none"
    assert c["animation"]["perCharacter"] is True


def test_generic_scroll_is_not_flagged() -> None:
    """scrollYProgress alone (generic scroll, no per-character split) must NOT
    produce a candidate — avoids false signature effects."""
    bundle = "const {scrollYProgress}=useScroll();const y=useTransform(scrollYProgress,[0,1],[0,100]);"
    assert sigfx.extract_candidates([bundle]) == []


def test_detects_per_word_scroll_highlight() -> None:
    """Words/lines toggling between a highlighted and a dimmed colour as scroll
    progress advances (highlighted+dimmed class pair + word split + scroll) must
    be detected as a per-word-scroll-highlight candidate."""
    bundle = (
        'function W(){let{scrollYProgress:w}=useScroll({target:r});'
        'let words=t.split(" ");'
        'return words.map((x,i)=><span className={i<a?s.line_highlighted:s.line_dimmed}>{x}</span>)}'
    )
    cands = sigfx.extract_candidates([bundle])
    pw = [c for c in cands if c["effectType"] == "per-word-scroll-highlight"]
    assert len(pw) == 1, cands
    c = pw[0]
    assert c["animation"]["perWord"] is True
    assert c["confidence"] == "high"  # has the word split
    assert c["library"] == "framer-motion"


def test_highlight_dim_without_scroll_not_flagged() -> None:
    """A static highlighted/dimmed class pair with NO scroll binding must NOT be
    flagged as a scroll effect."""
    bundle = 'const cls = active ? "line_highlighted" : "line_dimmed"; words.split(" ");'
    assert [c for c in sigfx.extract_candidates([bundle])
            if c["effectType"] == "per-word-scroll-highlight"] == []


def test_empty_and_no_signal() -> None:
    assert sigfx.extract_candidates([]) == []
    assert sigfx.extract_candidates(["function noop(){return 1}"]) == []


def test_real_omx33_bundle_if_present() -> None:
    """Integration: on the real loop ref (if still on disk) the per-char effect
    is detected. Skipped when the scratch dir is gone."""
    import glob
    refs = glob.glob(str(ROOT / "scratch" / "loop-omx-33" / "tmp" / "ref" / "*" / "bundles"))
    if not refs:
        import pytest
        pytest.skip("loop ref not on disk")
    ref_dir = Path(refs[0]).parent
    result = sigfx.parse_ref(ref_dir)
    assert any(c["effectType"] == "per-character-scroll-scrub" for c in result["candidates"]), result
