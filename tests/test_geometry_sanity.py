from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ui_clone.gates.geometry_sanity import evaluate, parse_viewport, section_anchor_name


def test_parse_viewport_reads_frozen_capture_shape() -> None:
    assert parse_viewport("1440x900") == (1440, 900)
    assert parse_viewport("bad") is None


def test_geometry_sanity_imports_under_macos_system_python() -> None:
    """Canonical shell gates can run through /usr/bin/python3 on macOS."""
    host_python = shutil.which("python3")
    if Path("/usr/bin/python3").exists():
        host_python = "/usr/bin/python3"
    if not host_python:
        pytest.skip("python3 not available")

    proc = subprocess.run(
        [
            host_python,
            "-c",
            "import importlib; importlib.import_module('ui_clone.gates.geometry_sanity')",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_section_anchor_names_keep_repeated_classes_distinct() -> None:
    first = {"index": 1, "tag": "div", "className": "evo-grid"}
    second = {"index": 2, "tag": "div", "className": "evo-grid"}
    assert section_anchor_name(first) == "evo-grid#1"
    assert section_anchor_name(second) == "evo-grid#2"


def test_section_anchor_names_drop_anonymous_generic_nodes() -> None:
    assert section_anchor_name({"index": 7, "tag": "div", "className": ""}) is None
    assert section_anchor_name({"index": 0, "tag": "header"}) == "header#0"


def test_loop129_balloon_class_fails() -> None:
    """The motivating failure: loop-129 scored its best dSSIM (0.1156) while
    docH was ~2.2x the ref — pixel metrics compare what IS rendered, not how
    much page exists. A ballooned docH must FAIL the geometry gate."""
    res = evaluate(17952, 39092, [])
    assert res["docH"]["status"] == "fail"
    assert res["status"] == "fail"


def test_close_geometry_passes() -> None:
    # the post-Fix-80 state: docH 18272 vs 17952 = 1.8% off
    res = evaluate(
        17952, 18272,
        [
            {"name": "solvable", "refH": 1899, "implH": 1424 + 475},  # ~0% off
            {"name": "hero", "refH": 638, "implH": 640},
        ],
    )
    assert res["status"] == "pass", res


def test_warn_band_between_warn_and_fail() -> None:
    # docH 12% off -> warn (between 10 and 15). 5000px ref keeps the delta
    # (600px) under the absolute px cap so the relative band governs.
    res = evaluate(5000, 5600, [])
    assert res["docH"]["status"] == "warn"
    assert res["status"] == "warn"


def test_major_section_drift_fails_even_with_good_doch() -> None:
    # one section +42% (2700 vs 1899) while docH compensates elsewhere
    res = evaluate(
        17952, 18000,
        [{"name": "solvable", "refH": 1899, "implH": 2700}],
    )
    sec = res["sections"][0]
    assert sec["status"] == "fail", sec
    assert res["status"] == "fail"


def test_minor_sections_ignored() -> None:
    # a 41px banner strip off by 3x is chrome noise, not drift
    res = evaluate(
        17952, 18000,
        [{"name": "usg-banner", "refH": 41, "implH": 120}],
    )
    assert res["sections"] == []
    assert res["status"] == "pass"


def test_unmeasurable_major_section_degrades_to_warn() -> None:
    # a major section the impl page can't locate is never a silent pass
    res = evaluate(
        17952, 18000,
        [{"name": "pyramid", "refH": 4122, "implH": None}],
    )
    assert res["sections"][0]["status"] == "unmeasurable"
    assert res["status"] == "warn"
    assert res["unmeasuredMajorSections"] == 1


def test_thresholds_tunable() -> None:
    res = evaluate(10000, 11200, [], doch_fail_pct=11.0, doch_warn_pct=5.0)
    assert res["docH"]["status"] == "fail"
    res2 = evaluate(
        10000, 11200, [], doch_fail_pct=30.0, doch_warn_pct=20.0, doch_fail_px=2000.0
    )
    assert res2["docH"]["status"] == "pass"


def test_missing_doch_is_unmeasurable_warn() -> None:
    res = evaluate(None, 18000, [])
    assert res["docH"]["status"] == "unmeasurable"
    assert res["status"] == "warn"


def test_sticky_section_uses_scroll_range_height() -> None:
    """realfood loop-e2e-4: section-map stores the sticky's 300vh travel union
    (2550) under the sticky element's class, while the element's instant box is
    100vh-ish (900) in BOTH ref and impl. When the FROZEN ref height is itself a
    travel union (ref_is_range), the comparable extent is the live parent
    scroll-range."""
    from ui_clone.gates.geometry_sanity import resolve_impl_height

    assert resolve_impl_height(900.0, 2550.0, True, ref_is_range=True) == 2550.0


def test_sticky_resolution_keeps_real_drift_visible() -> None:
    from ui_clone.gates.geometry_sanity import evaluate, resolve_impl_height

    # a sticky whose scroll-range genuinely shrank must still fail (ref stored a
    # travel union, so range-to-range)
    impl_h = resolve_impl_height(900.0, 1800.0, True, ref_is_range=True)
    res = evaluate(20133, 20133, [{"name": "resources", "refH": 2550, "implH": impl_h}])
    assert res["sections"][0]["status"] == "fail"


def test_legacy_instant_box_sticky_self_passes() -> None:
    """realfood e2e-12 ref-vs-ref: the frozen section-map stored the sticky's
    INSTANT box (900, no position/stickyRangeH field), so ref_is_range defaults
    False. The live sticky's 2700 parent travel-union must NOT be substituted —
    compare instant box to instant box. Live ref == impl instant box, so this
    self-passes instead of a false 200% fail (the failing gate at HEAD b8078dc:
    refH=900 vs implH=2700)."""
    from ui_clone.gates.geometry_sanity import evaluate, resolve_impl_height

    impl_h = resolve_impl_height(900.0, 2700.0, True)  # ref_is_range default False
    assert impl_h == 900.0
    res = evaluate(20133, 20133, [{"name": "resources", "refH": 900, "implH": impl_h}])
    assert res["sections"][0]["status"] == "pass"
    assert res["status"] == "pass"


def test_sticky_dropped_on_impl_still_fails_when_ref_is_range() -> None:
    """A faithful ref stored a travel union (ref_is_range=True) but the impl
    dropped the sticky behavior (no parent range, instant box only) — resolve
    falls back to the collapsed instant box and the gate still FAILs."""
    from ui_clone.gates.geometry_sanity import evaluate, resolve_impl_height

    impl_h = resolve_impl_height(900.0, None, False, ref_is_range=True)
    assert impl_h == 900.0
    res = evaluate(20133, 20133, [{"name": "resources", "refH": 2700, "implH": impl_h}])
    assert res["sections"][0]["status"] == "fail"


def test_non_sticky_ignores_range_candidate() -> None:
    from ui_clone.gates.geometry_sanity import resolve_impl_height

    assert resolve_impl_height(900.0, 2550.0, False) == 900.0


def test_sticky_without_range_falls_back_to_element() -> None:
    from ui_clone.gates.geometry_sanity import resolve_impl_height

    assert resolve_impl_height(900.0, None, True) == 900.0


# ── omx postmortem hardening: absolute px cap + missing-section FAIL ────────


def test_doch_absolute_px_cap_catches_tall_page_shortfall() -> None:
    """1593px short on a ~20k ref is 7.9% — silently inside the 15% relative
    band, yet a whole dark section + footer were missing (omx postmortem).
    The effective fail threshold is min(FAIL_PCT% of ref, abs px cap)."""
    out = evaluate(20133, 18540, [])
    assert out["status"] == "fail", out


def test_doch_px_cap_tunable_and_relative_band_still_governs_short_pages() -> None:
    # short page: 5% of 2000 = 100px diff — inside both bands
    ok = evaluate(2000, 1900, [])
    assert ok["status"] == "pass", ok
    # cap raised: the 1593px shortfall passes the px cap but stays in the
    # relative bands (7.9% < warn 10%) -> pass
    relaxed = evaluate(20133, 18540, [], doch_fail_px=2000.0)
    assert relaxed["status"] == "pass", relaxed


def test_missing_anchor_section_is_fail_not_warn() -> None:
    """A wholly missing section used to resolve implH=None -> 'unmeasurable'
    -> overall WARN. With the impl DOM anchor confirmed ABSENT, it is a FAIL."""
    out = evaluate(
        20000,
        20000,
        [{"name": "work-notes", "refH": 900, "implH": None, "anchorFound": False}],
    )
    assert out["status"] == "fail", out
    row = next(r for r in out["sections"] if r["name"] == "work-notes")
    assert row["status"] == "fail"


def test_unmeasurable_without_anchor_info_stays_warn() -> None:
    """Old artifacts without anchorFound keep the warn semantics."""
    out = evaluate(
        20000,
        20000,
        [{"name": "legacy", "refH": 900, "implH": None}],
    )
    assert out["status"] == "warn", out


def test_probe_uses_semantic_landmarks_and_explicit_anchor_presence() -> None:
    """Bare header/main/footer rows remain measurable without id/class anchors.

    The section-map extractor records their semantic tag. Geometry probing must
    use that stable selector and must distinguish a resolved zero-height element
    from a truly absent major landmark.
    """
    script = Path("skills/visual-debug/scripts/geometry-sanity-check.sh").read_text(
        encoding="utf-8"
    )

    assert 'tag in {"header", "main", "footer"}' in script
    assert "document.querySelectorAll(sec.tag)" in script
    assert "anchorFound: Boolean(el)" in script
    assert 'if "anchorFound" in m' in script


def test_geometry_sanity_is_strict_and_status_required() -> None:
    """rapid phase used to downgrade geometry-sanity block->warn, and a
    status-less artifact vacuously passed — both let the omx shortfall ship."""
    src = open("ui_clone/gates/verification_plan.py", encoding="utf-8").read()
    import re

    strict = re.search(r"STRICT_ALWAYS = \{(.*?)\}", src, re.S)
    required = re.search(r"STATUS_REQUIRED = \{(.*?)\}", src, re.S)
    assert strict and '"geometry-sanity"' in strict.group(1)
    assert required and '"geometry-sanity"' in required.group(1)
