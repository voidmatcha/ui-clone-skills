"""Geometry sanity — rendered page/section heights must track the ref capture.

Whole-page dSSIM and per-section AE miss a structural failure class: a build
can score its best dSSIM while the document is 2x the ref's height (loop-129:
best 0.1156 with docH ballooned), because pixel metrics compare what IS
rendered, not how much page exists. This gate compares the built impl's
rendered docH and per-section heights against the ref capture's geometry
(section-map heights + orig-layout totalHeight), measured at the capture
viewport so vh-authored tracks compare apples-to-apples.

Verdict bands per dimension: pass under the warn threshold, warn between warn
and fail, fail beyond. Thresholds are caller-tunable (env in the bash driver):
docH fail default 15% (warn 10%), section fail default 25% (warn 16%); only
MAJOR sections (>= min_section_px) are judged so chrome-strip noise is ignored.
"""
from __future__ import annotations

DOCH_FAIL_PCT_DEFAULT = 15.0
DOCH_WARN_PCT_DEFAULT = 10.0
SECTION_FAIL_PCT_DEFAULT = 25.0
SECTION_WARN_PCT_DEFAULT = 16.0
MIN_SECTION_PX_DEFAULT = 200.0


def _pct_off(ref: float, impl: float) -> float:
    if ref <= 0:
        return 0.0
    return abs(impl - ref) / ref * 100.0


def _band(pct: float, warn_at: float, fail_at: float) -> str:
    if pct > fail_at:
        return "fail"
    if pct > warn_at:
        return "warn"
    return "pass"


def evaluate(
    ref_doch: float | None,
    impl_doch: float | None,
    sections: list[dict],
    *,
    doch_fail_pct: float = DOCH_FAIL_PCT_DEFAULT,
    doch_warn_pct: float = DOCH_WARN_PCT_DEFAULT,
    section_fail_pct: float = SECTION_FAIL_PCT_DEFAULT,
    section_warn_pct: float = SECTION_WARN_PCT_DEFAULT,
    min_section_px: float = MIN_SECTION_PX_DEFAULT,
) -> dict:
    """sections: [{name, refH, implH|None}] — implH None = not measurable
    (counted separately, never a silent pass)."""
    rows: list[dict] = []
    statuses: list[str] = []

    doch_row: dict = {"name": "docH", "refH": ref_doch, "implH": impl_doch}
    if ref_doch and impl_doch:
        pct = _pct_off(ref_doch, impl_doch)
        doch_row["pctOff"] = round(pct, 1)
        doch_row["status"] = _band(pct, doch_warn_pct, doch_fail_pct)
    else:
        doch_row["status"] = "unmeasurable"
    statuses.append(str(doch_row["status"]))

    unmeasured = 0
    for sec in sections:
        ref_h = sec.get("refH")
        impl_h = sec.get("implH")
        if not isinstance(ref_h, int | float) or ref_h < min_section_px:
            continue  # minor strip — not a geometry-drift signal
        row = {"name": str(sec.get("name", "")), "refH": ref_h, "implH": impl_h}
        if isinstance(impl_h, int | float) and impl_h > 0:
            pct = _pct_off(float(ref_h), float(impl_h))
            row["pctOff"] = round(pct, 1)
            row["status"] = _band(pct, section_warn_pct, section_fail_pct)
        else:
            row["status"] = "unmeasurable"
            unmeasured += 1
        rows.append(row)
        statuses.append(str(row["status"]))

    if "fail" in statuses:
        status = "fail"
    elif "warn" in statuses or "unmeasurable" in statuses:
        # anything unmeasurable (docH or a major section) degrades to warn —
        # never a silent pass
        status = "warn"
    else:
        status = "pass"

    return {
        "schemaVersion": 1,
        "status": status,
        "docH": doch_row,
        "sections": rows,
        "unmeasuredMajorSections": unmeasured,
        "thresholds": {
            "dochFailPct": doch_fail_pct,
            "dochWarnPct": doch_warn_pct,
            "sectionFailPct": section_fail_pct,
            "sectionWarnPct": section_warn_pct,
            "minSectionPx": min_section_px,
        },
        "rule": (
            "Rendered impl geometry must track the ref capture: docH within "
            f"{doch_fail_pct}% and every major section height within "
            f"{section_fail_pct}% (warn band below that). Catches ballooned/"
            "collapsed pages that pixel metrics structurally miss."
        ),
    }
