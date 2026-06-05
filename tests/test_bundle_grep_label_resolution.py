from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.post_implement import _ref_source_patterns_for_label

SECMAP = {
    "sections": [
        {"index": 1, "tag": "section", "className": "dga_hero__AjMaf", "id": None},
        {"index": 3, "tag": "div", "className": "dga_broken_system_wrapper__OOvVj", "id": None},
        {"index": 0, "tag": "div", "className": "intro-animation_overlay___QI3A", "id": None},
        {"index": 11, "tag": "section", "className": "dga_end___VNIF", "id": "footer"},
        {"index": 12, "tag": "section", "className": "dga_section__k3uwv dga_cta__6_hMx", "id": "footer"},
    ]
}


def _ref(tmp_path: Path) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps(SECMAP), encoding="utf-8")
    return ref


def test_sanitized_class_label_resolves_to_real_css_token(tmp_path: Path) -> None:
    """section-compare sanitizes CSS-module class names by collapsing
    underscore runs (dga_hero__AjMaf -> dga_hero_AjMaf), so grepping the label
    verbatim misses the ref css/bundles and the fix-loop reports 'no ref-source
    matches' for the worst-AE sections. The resolver must map the label back to
    the real token via section-map.json."""
    ref = _ref(tmp_path)
    pats = _ref_source_patterns_for_label(ref, "dga_hero_AjMaf")
    assert pats[0] == "dga_hero__AjMaf", pats
    assert "dga_hero_AjMaf" in pats  # raw label kept as fallback

    pats = _ref_source_patterns_for_label(ref, "dga_broken_system_wrapper_OOvVj")
    assert pats[0] == "dga_broken_system_wrapper__OOvVj", pats

    # triple underscore collapses too
    pats = _ref_source_patterns_for_label(ref, "intro-animation_overlay_QI3A")
    assert pats[0] == "intro-animation_overlay___QI3A", pats


def test_dedupe_suffix_label_resolves_to_id_and_class(tmp_path: Path) -> None:
    """Duplicate section ids gain a -N suffix in the comparer (footer-2); the
    resolver must strip it and surface the real id plus the section's class
    token so the grep has real candidates."""
    ref = _ref(tmp_path)
    pats = _ref_source_patterns_for_label(ref, "footer-2")
    assert "footer" in pats, pats
    # both footer-labelled sections' class tokens are candidates
    assert any(p.startswith("dga_") for p in pats), pats
    assert pats[-1] == "footer-2"  # raw label last (fallback)


def test_unknown_label_falls_back_to_raw(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    pats = _ref_source_patterns_for_label(ref, "totally-unknown")
    assert pats == ["totally-unknown"]


def test_missing_section_map_falls_back_to_raw(tmp_path: Path) -> None:
    ref = tmp_path / "empty"
    ref.mkdir()
    pats = _ref_source_patterns_for_label(ref, "dga_hero_AjMaf")
    assert pats == ["dga_hero_AjMaf"]
