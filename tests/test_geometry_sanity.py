from __future__ import annotations

from ui_clone.gates.geometry_sanity import evaluate


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
    # docH 12% off -> warn (between 10 and 15)
    res = evaluate(10000, 11200, [])
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
    res2 = evaluate(10000, 11200, [], doch_fail_pct=30.0, doch_warn_pct=20.0)
    assert res2["docH"]["status"] == "pass"


def test_missing_doch_is_unmeasurable_warn() -> None:
    res = evaluate(None, 18000, [])
    assert res["docH"]["status"] == "unmeasurable"
    assert res["status"] == "warn"
