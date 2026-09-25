"""Cross-section cumulative-drift diagnostic (read-only, section-compare).

Moved verbatim out of ``ui_clone.section_compare_sections``; that module
re-exports every name here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ui_clone.section_compare_common import Section, _is_number

# ── Cross-section cumulative-drift diagnostic (read-only) ──
# The matcher pairs ref<->impl sections but offers no view of the cumulative
# vertical drift that turns one baked/collapsed section into a cascade of
# downstream "victim" sections — each shifted by every height error above it.
# This is a purely ADDITIVE observation surface: it never touches matches.json
# contents, the pairing, any verdict, or any gate. It only reads the final
# paired list and emits a sidecar table + a stdout summary so a human/agent can
# see WHERE drift was injected and which earlier section's height delta most
# plausibly caused each jump.
_DRIFT_JUMP_EPSILON = 8.0


def _rect_top(side: object) -> float | None:
    if not isinstance(side, dict):
        return None
    rect = side.get("rect")
    if not isinstance(rect, dict):
        return None
    value = rect.get("top")
    if isinstance(value, bool) or not _is_number(value):
        return None
    return float(value)


def _rect_height(side: object) -> float | None:
    if not isinstance(side, dict):
        return None
    rect = side.get("rect")
    if not isinstance(rect, dict):
        return None
    value = rect.get("height")
    if isinstance(value, bool) or not _is_number(value):
        return None
    return float(value)


def build_drift_diagnostic(
    matches: Sequence[Section], jump_epsilon: float = _DRIFT_JUMP_EPSILON
) -> dict[str, Any]:
    """Produce a read-only cross-section cumulative-drift diagnostic.

    Given the final paired list, build a table sorted by ref top with per-pair
    drift (impl top - ref top) and height delta (impl height - ref height), and
    attribute each running-drift JUMP to the PREVIOUS in-order section's height
    delta — the section whose baked/extra height (positive hDelta) or
    collapsed-overlap / dropped negative-margin (negative hDelta) pushed every
    following section down.

    Pure observation: callers must not feed the result back into pairing,
    verdicts, or gating. Pairs missing a top on either side carry null drift and
    are skipped for jump attribution (never crash).
    """
    rows: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        ref = match.get("ref")
        impl = match.get("impl")
        ref_top = _rect_top(ref)
        impl_top = _rect_top(impl)
        ref_h = _rect_height(ref)
        impl_h = _rect_height(impl)
        drift = (
            impl_top - ref_top
            if ref_top is not None and impl_top is not None
            else None
        )
        h_delta = (
            impl_h - ref_h if ref_h is not None and impl_h is not None else None
        )
        rows.append(
            {
                "name": str(match.get("name") or ""),
                "refTop": ref_top,
                "implTop": impl_top,
                "drift": drift,
                "refH": ref_h,
                "implH": impl_h,
                "hDelta": h_delta,
                "score": match.get("score"),
            }
        )

    # Sort by ref top so the table reads top-to-bottom of the page. Rows with no
    # ref top sink to the end (they have no place in the vertical cascade) while
    # staying in the table for completeness.
    table = sorted(
        rows,
        key=lambda r: (r["refTop"] is None, r["refTop"] if r["refTop"] is not None else 0.0),
    )

    jumps: list[dict[str, Any]] = []
    prev_drift: float | None = None
    prev_row: dict[str, Any] | None = None
    for row in table:
        drift = row["drift"]
        if drift is None:
            # Cannot place this pair in the running cascade; reset the chain so a
            # later pair is not compared against a stale drift across a gap.
            prev_drift = None
            prev_row = None
            continue
        if (
            prev_drift is not None
            and prev_row is not None
            and drift - prev_drift > jump_epsilon
        ):
            cause_h_delta = prev_row.get("hDelta")
            if _is_number(cause_h_delta) and cause_h_delta < 0:
                cause = "dropped negative-margin / collapsed-overlap"
            else:
                cause = "baked/extra height"
            jumps.append(
                {
                    "at": row["name"],
                    "cause": prev_row["name"],
                    "causeHDelta": cause_h_delta,
                    "driftIncrease": round(drift - prev_drift, 4),
                    "fromDrift": prev_drift,
                    "toDrift": drift,
                    "reason": cause,
                }
            )
        prev_drift = drift
        prev_row = row

    drifts = [r["drift"] for r in table if _is_number(r["drift"])]
    total_drift_range = {
        "min": min(drifts) if drifts else None,
        "max": max(drifts) if drifts else None,
    }

    return {
        "table": table,
        "jumps": jumps,
        "totalDriftRange": total_drift_range,
    }


def _format_drift_value(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def print_drift_diagnostic(diagnostic: dict[str, Any]) -> None:
    """Print a compact human-readable cumulative-drift table to stdout."""
    table = diagnostic.get("table") or []
    jumps = diagnostic.get("jumps") or []
    if not table:
        return
    print("  cumulative drift (impl top - ref top), sorted by ref top:")
    print(
        f"    {'name':<28} {'refTop':>8} {'implTop':>8} "
        f"{'drift':>7} {'hDelta':>8} {'score':>6}"
    )
    for row in table:
        print(
            f"    {str(row.get('name') or '')[:28]:<28} "
            f"{_format_drift_value(row.get('refTop')):>8} "
            f"{_format_drift_value(row.get('implTop')):>8} "
            f"{_format_drift_value(row.get('drift')):>7} "
            f"{_format_drift_value(row.get('hDelta')):>8} "
            f"{_format_drift_value(row.get('score')):>6}"
        )
    if jumps:
        print("  drift jumps (running drift increased > epsilon):")
        for jump in jumps:
            print(
                f"    +{_format_drift_value(jump.get('driftIncrease'))} at "
                f"'{jump.get('at')}' <- '{jump.get('cause')}' "
                f"(hDelta={_format_drift_value(jump.get('causeHDelta'))}, "
                f"{jump.get('reason')})"
            )
