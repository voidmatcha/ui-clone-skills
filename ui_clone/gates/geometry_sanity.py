"""Geometry sanity — rendered page/section heights must track the ref capture.

Whole-page dSSIM and per-section AE miss a structural failure class: a build
can score its best dSSIM while the document is 2x the ref's height (specific regression:
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

import json
import re
from pathlib import Path

DOCH_FAIL_PCT_DEFAULT = 15.0
DOCH_WARN_PCT_DEFAULT = 10.0
# Absolute docH shortfall cap (omx postmortem): 1593px missing on a ~20k ref
# is 7.9% — inside the relative band — yet a whole section + footer were
# gone. Effective fail threshold = min(FAIL_PCT% of ref, this cap); the
# live-parity max(pct,px) tolerance pattern, inverted for tall pages.
# Env-tunable via UI_CLONE_GEOM_DOCH_FAIL_PX in the shell check.
DOCH_FAIL_PX_DEFAULT = 800.0
SECTION_FAIL_PCT_DEFAULT = 25.0
SECTION_WARN_PCT_DEFAULT = 16.0
MIN_SECTION_PX_DEFAULT = 200.0
NUMERIC_TYPES = (int, float)


def parse_viewport(value: object) -> tuple[int, int] | None:
    """Parse a frozen capture viewport such as ``1440x900``."""
    match = re.fullmatch(r"\s*(\d+)x(\d+)\s*", str(value or ""))
    if not match:
        return None
    width, height = (int(part) for part in match.groups())
    return (width, height) if width > 0 and height > 0 else None


def _viewport_pair(value: object) -> tuple[int, int] | None:
    if isinstance(value, dict):
        width = value.get("w", value.get("width"))
        height = value.get("h", value.get("height"))
        if isinstance(width, NUMERIC_TYPES) and isinstance(height, NUMERIC_TYPES):
            width, height = int(width), int(height)
            return (width, height) if width > 0 and height > 0 else None
    return parse_viewport(value)


def capture_viewport(ref_dir: str | Path) -> tuple[int, int]:
    """Resolve the viewport used by the frozen capture artifacts."""
    ref_path = Path(ref_dir)

    try:
        layout = json.loads((ref_path / "orig-layout.json").read_text(encoding="utf-8"))
        pair = _viewport_pair(
            {"w": layout.get("viewportWidth"), "h": layout.get("viewportHeight")}
        )
        if pair:
            return pair
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    try:
        context = json.loads(
            (ref_path / "container-context.json").read_text(encoding="utf-8")
        )
        pair = _viewport_pair(context.get("viewport"))
        if pair:
            return pair
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    try:
        provenance = json.loads(
            (ref_path / "sections" / "frozen-ref-provenance.json").read_text(
                encoding="utf-8"
            )
        )
        pair = _viewport_pair(provenance.get("viewport"))
        if pair:
            return pair
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    return (1280, 800)


def section_anchor_name(section: dict) -> str | None:
    """Return a stable unique key for an anchorable section-map row.

    Repeated utility classes such as ``evo-grid`` are common. Using the class
    alone collapses several measured rows into one dictionary entry, while
    anonymous generic divs cannot be located honestly in the impl at all.
    """
    element_id = str(section.get("id") or "").strip()
    if element_id:
        return element_id

    classes = str(section.get("className") or section.get("cls") or "").split()
    tag = str(section.get("tag") or section.get("tagName") or "").lower()
    base = classes[0] if classes else tag if tag in {"header", "main", "footer"} else ""
    if not base:
        return None

    index = section.get("index")
    return f"{base}#{index}" if index is not None else base


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


def resolve_impl_height(
    impl_h: float | None,
    sticky_range_h: float | None,
    is_sticky: bool,
    ref_is_range: bool = False,
) -> float | None:
    """Pick the impl height comparable to the ref capture's stored height.

    A position:sticky section has TWO heights: its instant box (100vh-ish) and
    its TRAVEL UNION (the parent scroll-range it pins through — e.g. a 300vh
    section). The comparison must be symmetric to whatever the FROZEN ref
    actually stored:

    - When the ref's stored height IS the travel union (``ref_is_range``), the
      comparable impl extent is the live parent scroll-range — the instant box
      would fail by construction (loop-e2e-4: ref 2550 vs impl 900 while live
      ref == live impl at every viewport).
    - When the ref stored the element's INSTANT BOX (a sticky section recorded
      at ~900px with no position field), compare the live instant box against
      it. Substituting the live travel-union (~2700px) there is a false 200%
      fail — the ref disagreeing with its own frozen artifact over two
      incompatible height definitions.

    ``ref_is_range`` defaults False so legacy artifacts lacking the field
    compare instant-box-to-instant-box (the self-pass case). Deterministic —
    never "whichever is closer", so real range drift still fails.
    """
    if (
        is_sticky
        and ref_is_range
        and isinstance(sticky_range_h, NUMERIC_TYPES)
        and sticky_range_h > 0
    ):
        return float(sticky_range_h)
    return impl_h


def evaluate(
    ref_doch: float | None,
    impl_doch: float | None,
    sections: list[dict],
    *,
    doch_fail_pct: float = DOCH_FAIL_PCT_DEFAULT,
    doch_warn_pct: float = DOCH_WARN_PCT_DEFAULT,
    doch_fail_px: float = DOCH_FAIL_PX_DEFAULT,
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
        # Absolute px overlay: on tall pages the relative band hides a
        # whole missing section. Effective fail threshold is
        # min(fail_pct% of ref, doch_fail_px).
        delta_px = abs(float(ref_doch) - float(impl_doch))
        effective_fail_px = min(ref_doch * doch_fail_pct / 100.0, doch_fail_px)
        if delta_px > effective_fail_px:
            doch_row["deltaPx"] = round(delta_px, 1)
            doch_row["status"] = "fail"
    else:
        doch_row["status"] = "unmeasurable"
    statuses.append(str(doch_row["status"]))

    unmeasured = 0
    for sec in sections:
        ref_h = sec.get("refH")
        impl_h = sec.get("implH")
        if not isinstance(ref_h, NUMERIC_TYPES) or ref_h < min_section_px:
            continue  # minor strip — not a geometry-drift signal
        row = {"name": str(sec.get("name", "")), "refH": ref_h, "implH": impl_h}
        if isinstance(impl_h, NUMERIC_TYPES) and impl_h > 0:
            pct = _pct_off(float(ref_h), float(impl_h))
            row["pctOff"] = round(pct, 1)
            row["status"] = _band(pct, section_warn_pct, section_fail_pct)
        elif sec.get("anchorFound") is False:
            # The impl DOM was probed and the section's id/class anchor is
            # ABSENT — that is a missing section (omx postmortem: dark
            # work-notes section gone -> implH null -> "unmeasurable" ->
            # overall WARN shipped). Hard fail, not a measurement gap.
            row["status"] = "fail"
            row["anchorFound"] = False
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
