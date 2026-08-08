# mypy: disable-error-code="no-untyped-def,no-untyped-call,type-arg,operator,no-any-return"

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])

# Tolerances are env-tunable per repo convention; defaults preserve the
# calibrated values (loop-9 regression corpus). A LARGER tolerance is more
# lenient, so each env override is clamped to a max that cannot be opened wide
# enough to pass a real off-center defect (loop-9 class is >=64px) — env
# leniency is itself a bypass surface (batch-7 ITEM 4).
def _clamp_tol(env, default, ceiling):
    try:
        return min(ceiling, max(0.0, float(os.environ.get(env, default))))
    except (TypeError, ValueError):
        return float(default)


CENTER_TOL_PX = _clamp_tol("UI_CLONE_ALIGN_CENTER_PX", "16", 40.0)
CENTER_TOL_PCT = _clamp_tol("UI_CLONE_ALIGN_CENTER_PCT", "1.25", 2.5)
GAP_TOL_PX = _clamp_tol("UI_CLONE_ALIGN_GAP_PX", "12", 32.0)
GAP_TOL_PCT = _clamp_tol("UI_CLONE_ALIGN_GAP_PCT", "1.0", 2.5)

RULE = (
    "Matched sections must keep the ref's horizontal alignment: section-box "
    "center offset within max(16px, 1.25% vpW) of the ref's, and contentBox "
    "left/right gap asymmetry within max(12px, 1% section width) of the "
    "ref's (ref-relative — intentionally asymmetric refs compare as-is)."
)
REMEDIATION = (
    "ref recapture needed — re-run section-compare.sh enumeration with the "
    "contentBox-enabled enumerator so ref rows carry contentBox/leftGap/"
    "rightGap fields."
)


def write(payload: dict[str, Any], code: int) -> None:
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.exit(code)


def _viewport_sources() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    vp_root = ref_dir / "sections" / "viewports"
    if vp_root.is_dir():
        for d in sorted(vp_root.iterdir()):
            m = d / "sections" / "matches.json"
            if m.is_file():
                out.append((d.name, m))
    if not out:
        m = ref_dir / "sections" / "matches.json"
        if m.is_file():
            out.append(("default", m))
    return out


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _gaps(row: dict) -> tuple[float, float] | None:
    """leftGap/rightGap from the row, deriving from contentBox when needed."""
    lg, rg = _num(row.get("leftGap")), _num(row.get("rightGap"))
    if lg is not None and rg is not None:
        return lg, rg
    cb = row.get("contentBox")
    rect = row.get("rect")
    if not isinstance(cb, dict) or not isinstance(rect, dict):
        return None
    c_left, c_width = _num(cb.get("left")), _num(cb.get("width"))
    r_left, r_width = _num(rect.get("left")), _num(rect.get("width"))
    if None in (c_left, c_width, r_left, r_width):
        return None
    return c_left - r_left, (r_left + r_width) - (c_left + c_width)


def _content_bearing(row: dict) -> bool:
    text = str(row.get("textWords") or row.get("fingerprint") or "").strip()
    try:
        children = int(row.get("childCount") or 0)
    except (TypeError, ValueError):
        children = 0
    return bool(text) or children > 0


def _client_width(row: dict, viewport: str) -> float | None:
    cw = _num(row.get("clientWidth"))
    if cw is not None and cw > 0:
        return cw
    try:
        return float(int(viewport.split("x")[0]))
    except (ValueError, IndexError):
        return None


def _fully_horizontally_off_canvas(rect: dict, client_width: float | None) -> bool:
    """Return true when a section has no horizontal intersection with the viewport.

    Closed drawers are commonly parked one or more viewport widths to the left
    or right while retaining full layout geometry. Their inner alignment is not
    visible and can legitimately vary with the drawer's responsive width.
    """
    if client_width is None or client_width <= 0:
        return False
    left = _num(rect.get("left"))
    width = _num(rect.get("width"))
    if left is None or width is None:
        return False
    return left + width <= 0 or left >= client_width


def _is_overflow_group(group: dict) -> bool:
    """An OVERFLOW scroll-track: the child union envelope materially exceeds its
    container width, so the content extends beyond (and is clipped / scrolled off)
    the container. "Horizontal centering within the container" is undefined for
    such a track — its off-screen extent and start offset are NOT a visible
    alignment property and legitimately differ between two captures (or a faithful
    flattened-grid impl whose overflow strip starts a few px over) — so the
    per-group centering prongs (group-asym / group-childshift / group-leftover)
    are exempted. The VISIBLE box stays measured by the section-center and
    contentbox-asym prongs. Gated strictly on union > container + the alignment
    tolerance, so a centered/fitting group (union <= container) is NEVER exempted
    and real off-center defects still fail (loop-9 class: union sits well INSIDE
    its container, ratio < 1)."""
    cw = _num(group.get("containerWidth"))
    uw = _num(group.get("unionWidth"))
    if cw is None or uw is None or cw <= 0:
        return False
    margin = max(GAP_TOL_PX, GAP_TOL_PCT / 100.0 * cw)
    return uw > cw + margin


def _dynamic_group_tokens() -> set[str]:
    """Class-token bases of every dynamic:true transition-spec target. A
    contentGroup whose name matches one lives inside a DECLARED animated region
    (timer carousel / scroll-scrub): its inner position and count vary across
    reference loads (a food carousel whose card groups translate and re-order
    every cycle), so it cannot be alignment-verified ref-vs-ref.
    (batch-13 ITEM 3 — only spec-DECLARED dynamic regions are exempted, so static
    content alignment is still enforced.)"""
    spec = ref_dir / "transition-spec.json"
    tokens: set[str] = set()
    try:
        data = json.loads(spec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return tokens
    transitions = data.get("transitions", []) if isinstance(data, dict) else []
    for t in transitions:
        if not isinstance(t, dict) or t.get("dynamic") is not True:
            continue
        for sel in str(t.get("target") or "").replace(",", " ").split():
            for tok in sel.replace(".", " ").replace("#", " ").split():
                base = tok.strip().lower().split("__", 1)[0]
                if len(base) >= 4:
                    tokens.add(base)
    return tokens


_DYNAMIC_GROUP_TOKENS = _dynamic_group_tokens()


def _is_dynamic_group(group_name: str) -> bool:
    base = str(group_name or "").split("[", 1)[0].strip().lower().split("__", 1)[0]
    if len(base) < 4:
        return False
    return any(
        base == tok or base.startswith(tok) or tok.startswith(base)
        for tok in _DYNAMIC_GROUP_TOKENS
    )


def _drop_dynamic_groups(groups: Any) -> tuple[list, int]:
    """Return (static groups, dropped-dynamic count) — dropped groups belong to a
    declared dynamic region and are exempt from alignment."""
    if not isinstance(groups, list):
        return groups, 0
    kept = [g for g in groups if not (isinstance(g, dict) and _is_dynamic_group(g.get("name", "")))]
    return kept, len(groups) - len(kept)


sources = _viewport_sources()
if not sources:
    write(
        {
            "schemaVersion": 1,
            "status": "skip",
            "reason": "no sections/matches.json artifacts (section-compare not run)",
            "rows": [],
            "unmeasured": [],
            "rule": RULE,
        },
        0,
    )

rows: list[dict[str, Any]] = []
unmeasured: list[dict[str, Any]] = []
viewports_checked: list[str] = []

for viewport, matches_path in sources:
    try:
        matches = json.loads(matches_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        unmeasured.append(
            {
                "viewport": viewport,
                "section": "(all)",
                "reason": f"matches.json unreadable: {exc}",
            }
        )
        continue
    if not isinstance(matches, list):
        unmeasured.append(
            {
                "viewport": viewport,
                "section": "(all)",
                "reason": "matches.json is not a list",
            }
        )
        continue
    viewports_checked.append(viewport)

    for match in matches:
        if not isinstance(match, dict):
            continue
        ref_row = match.get("ref")
        impl_row = match.get("impl")
        if not isinstance(ref_row, dict) or not isinstance(impl_row, dict):
            continue  # unmatched rows are section-compare's concern, not alignment's
        name = str(match.get("name") or ref_row.get("className") or "section")
        ref_rect = ref_row.get("rect") if isinstance(ref_row.get("rect"), dict) else None
        impl_rect = impl_row.get("rect") if isinstance(impl_row.get("rect"), dict) else None
        if not ref_rect or not impl_rect:
            continue
        ref_cw = _client_width(ref_row, viewport)
        impl_cw = _client_width(impl_row, viewport)
        # Off-canvas synthetic pairs (settled splash overlays and closed
        # horizontal drawers) have nothing visible to align. Only skip when
        # BOTH sides are fully outside their own viewport; a ref-only or
        # impl-only escape remains a real section-center defect.
        try:
            if float(ref_rect.get("top", 0)) + float(ref_rect.get("height", 0)) <= 0:
                continue
        except (TypeError, ValueError):
            pass
        if (
            _fully_horizontally_off_canvas(ref_rect, ref_cw)
            and _fully_horizontally_off_canvas(impl_rect, impl_cw)
        ):
            continue

        # ── prong (a): section-box center offset, ref-relative ──
        r_left, r_width = _num(ref_rect.get("left")), _num(ref_rect.get("width"))
        i_left, i_width = _num(impl_rect.get("left")), _num(impl_rect.get("width"))
        if None not in (ref_cw, impl_cw, r_left, r_width, i_left, i_width):
            ref_offset = (r_left + r_width / 2) - ref_cw / 2
            impl_offset = (i_left + i_width / 2) - impl_cw / 2
            delta = abs(ref_offset - impl_offset)
            tol = max(CENTER_TOL_PX, CENTER_TOL_PCT / 100.0 * impl_cw)
            rows.append(
                {
                    "viewport": viewport,
                    "section": name,
                    "check": "section-center",
                    "refOffsetPx": round(ref_offset, 1),
                    "implOffsetPx": round(impl_offset, 1),
                    "deltaPx": round(delta, 1),
                    "tolerancePx": round(tol, 1),
                    "status": "fail" if delta > tol else "ok",
                }
            )

        # ── prong (b): contentBox gap asymmetry, ref-relative ──
        ref_gaps = _gaps(ref_row)
        impl_gaps = _gaps(impl_row)
        if ref_gaps is not None and impl_gaps is not None:
            ref_asym = ref_gaps[0] - ref_gaps[1]
            impl_asym = impl_gaps[0] - impl_gaps[1]
            delta = abs(impl_asym - ref_asym) / 2
            tol = max(GAP_TOL_PX, GAP_TOL_PCT / 100.0 * (r_width or 0))
            rows.append(
                {
                    "viewport": viewport,
                    "section": name,
                    "check": "contentbox-asym",
                    "refLeftGap": ref_gaps[0],
                    "refRightGap": ref_gaps[1],
                    "implLeftGap": impl_gaps[0],
                    "implRightGap": impl_gaps[1],
                    "deltaPx": round(delta, 1),
                    "tolerancePx": round(tol, 1),
                    "status": "fail" if delta > tol else "ok",
                }
            )
        elif _content_bearing(ref_row) or _content_bearing(impl_row):
            missing_side = "ref" if ref_gaps is None else "impl"
            unmeasured.append(
                {
                    "viewport": viewport,
                    "section": name,
                    "reason": (
                        f"contentBox fields absent on {missing_side} row — "
                        "frozen artifact predates the contentBox enumerator"
                    ),
                }
            )

        # ── prong (c): contentGroups asymmetry, ref-relative ──
        # A whole-section union is diluted by full-width centered siblings
        # (loop-9 eatReal: h2 spans the content column while the carousel
        # cards group is +64px off-center). Per-container child-union gaps
        # measure each group against ITS container, so narrow mis-centered
        # groups cannot hide behind wide centered ones.
        ref_groups = ref_row.get("contentGroups")
        impl_groups = impl_row.get("contentGroups")
        # Exempt groups inside a DECLARED dynamic:true region (carousel/scrub) —
        # their inner position/count is non-deterministic across reference loads
        # (the eatReal carousel cards), so alignment cannot be verified against
        # the reference itself. Static groups in the same section are still
        # checked; spec-declared dynamic regions only. (batch-13 ITEM 3)
        ref_groups, _ref_dyn_dropped = _drop_dynamic_groups(ref_groups)
        impl_groups, _ = _drop_dynamic_groups(impl_groups)
        if _ref_dyn_dropped:
            unmeasured.append(
                {
                    "viewport": viewport,
                    "section": name,
                    "reason": (
                        f"{_ref_dyn_dropped} contentGroup(s) inside a declared "
                        "dynamic:true region (animated carousel/scrub) exempt from "
                        "alignment — inner position varies across reference loads"
                    ),
                }
            )
        # Review-2 finding 1: absence of the contentGroups field is never a
        # silent skip. A content-bearing ref row without a groups LIST is
        # unmeasurable debt (recapture needed); an impl row without one while
        # the ref declares groups is evidence absence on the impl side.
        if not isinstance(ref_groups, list):
            if _content_bearing(ref_row):
                unmeasured.append(
                    {
                        "viewport": viewport,
                        "section": name,
                        "reason": (
                            "contentGroups field absent/non-list on the ref "
                            "row of a content-bearing section — group "
                            "alignment cannot be verified"
                        ),
                    }
                )
        elif not isinstance(impl_groups, list):
            if ref_groups:
                for idx_g in range(len(ref_groups)):
                    rows.append(
                        {
                            "viewport": viewport,
                            "section": name,
                            "check": "group-missing",
                            "group": f"(ref group {idx_g})",
                            "status": "fail",
                            "reason": (
                                "impl row carries no contentGroups list while "
                                "the ref declares groups — stripping the field "
                                "does not exempt group alignment from "
                                "verification"
                            ),
                        }
                    )
        elif isinstance(ref_groups, list) and isinstance(impl_groups, list):
            def _index(groups: list) -> dict[str, dict]:
                seen: dict[str, int] = {}
                out: dict[str, dict] = {}
                for g in groups:
                    if not isinstance(g, dict):
                        continue
                    base = str(g.get("name") or "group")
                    n = seen.get(base, 0)
                    seen[base] = n + 1
                    out[f"{base}[{n}]"] = g
                return out

            ref_idx = _index(ref_groups)
            impl_idx = _index(impl_groups)
            # Pairing: name-keyed first, then positional fallback for the
            # leftovers (review-1 MAJOR 1 — an impl that renames the
            # misaligned group must not skip the check). Ref groups with no
            # impl counterpart at all are a fail, not a silent skip.
            pairs: list[tuple[str, dict, dict]] = [
                (key, ref_idx[key], impl_idx[key])
                for key in sorted(ref_idx.keys() & impl_idx.keys())
            ]
            ref_leftover = [k for k in ref_idx if k not in impl_idx]
            impl_leftover = [k for k in impl_idx if k not in ref_idx]
            for ref_key, impl_key in zip(ref_leftover, impl_leftover):
                pairs.append(
                    (f"{ref_key}~{impl_key}", ref_idx[ref_key], impl_idx[impl_key])
                )
            # A ref-leftover group named after a BARE HTML TAG (no className token)
            # is a low-confidence layout container (carousel/wrapper div) whose
            # COUNT varies across loads — the reference's own two enumerations
            # disagree on it (e.g. the eatReal carousel: ref [content, cards, div]
            # vs [content, cards]). Downgrade THOSE to a non-blocking warn so the
            # reference's own DOM jitter is not a defect; a className-NAMED leftover
            # (real content group stripped/renamed) STILL FAILs. (batch-13 ITEM 3)
            _GENERIC_GROUP_TAGS = {
                "div", "span", "section", "article", "aside", "main",
                "figure", "ul", "ol", "li", "p", "group",
            }
            for ref_key in ref_leftover[len(impl_leftover):]:
                _base = ref_key.split("[", 1)[0].strip().lower()
                if _base in _GENERIC_GROUP_TAGS:
                    unmeasured.append(
                        {
                            "viewport": viewport,
                            "section": name,
                            "reason": (
                                f"ref-leftover layout group '{ref_key}' is a bare-tag "
                                "container whose count varies across reference loads "
                                "— not a measurable alignment defect"
                            ),
                        }
                    )
                    continue
                rows.append(
                    {
                        "viewport": viewport,
                        "section": name,
                        "check": "group-missing",
                        "group": ref_key,
                        "status": "fail",
                        "reason": (
                            "ref content group has no impl counterpart — "
                            "removing/renaming a container does not exempt "
                            "its alignment from verification"
                        ),
                    }
                )
            # Unpaired IMPL-leftover groups are NOT assumed benign (batch-6
            # ITEM 3). A decoy group carrying the ref's class token — or a
            # duplicate same-name group, or the real cards under a NEW class —
            # consumes the ref pairing and leaves the REAL off-center group as
            # an impl-leftover. Evaluate each such group against ITS OWN
            # container: a content-bearing impl group with no ref counterpart
            # that is off-center beyond tolerance (and so not ref-relative-
            # justified) fails, rather than being silently dropped.
            for impl_key in impl_leftover[len(ref_leftover):]:
                ig = impl_idx[impl_key]
                if _is_overflow_group(ig):
                    rows.append(
                        {
                            "viewport": viewport,
                            "section": name,
                            "check": "group-overflow",
                            "group": impl_key,
                            "status": "ok",
                            "reason": (
                                "impl-leftover group is an overflow scroll-track "
                                "(union exceeds its container) — horizontal "
                                "centering is undefined for clipped/scrolled "
                                "overflow content"
                            ),
                        }
                    )
                    continue
                i_cl = _num(ig.get("containerLeft"))
                i_cw = _num(ig.get("containerWidth"))
                i_ul = _num(ig.get("unionLeft"))
                i_uw = _num(ig.get("unionWidth"))
                if None in (i_cl, i_cw, i_ul, i_uw) or not i_cw:
                    continue
                i_lg = i_ul - i_cl
                i_rg = (i_cl + i_cw) - (i_ul + i_uw)
                asym = abs(i_lg - i_rg) / 2
                tol = max(GAP_TOL_PX, GAP_TOL_PCT / 100.0 * i_cw)
                rows.append(
                    {
                        "viewport": viewport,
                        "section": name,
                        "check": "group-leftover",
                        "group": impl_key,
                        "implLeftGap": round(i_lg, 1),
                        "implRightGap": round(i_rg, 1),
                        "deltaPx": round(asym, 1),
                        "tolerancePx": round(tol, 1),
                        "status": "fail" if asym > tol else "ok",
                        "reason": (
                            "impl content group with no ref counterpart is "
                            "off-center in its own container — a decoy or "
                            "renamed group must not let the real off-center "
                            "content be silently dropped from alignment parity"
                        )
                        if asym > tol
                        else None,
                    }
                )
            for key, rg, ig in pairs:
                if _is_overflow_group(rg):
                    rows.append(
                        {
                            "viewport": viewport,
                            "section": name,
                            "check": "group-overflow",
                            "group": key,
                            "status": "ok",
                            "reason": (
                                "ref content group is an overflow scroll-track "
                                "(union exceeds its container) — horizontal "
                                "centering within the container is undefined; the "
                                "visible box is still measured by section-center / "
                                "contentbox-asym"
                            ),
                        }
                    )
                    continue
                vals = [
                    _num(rg.get("containerLeft")), _num(rg.get("containerWidth")),
                    _num(rg.get("unionLeft")), _num(rg.get("unionWidth")),
                    _num(ig.get("containerLeft")), _num(ig.get("containerWidth")),
                    _num(ig.get("unionLeft")), _num(ig.get("unionWidth")),
                ]
                if None in vals:
                    continue
                (r_cl, r_cw, r_ul, r_uw, i_cl, i_cw, i_ul, i_uw) = vals
                r_lg = r_ul - r_cl
                r_rg = (r_cl + r_cw) - (r_ul + r_uw)
                i_lg = i_ul - i_cl
                i_rg = (i_cl + i_cw) - (i_ul + i_uw)
                delta = abs((i_lg - i_rg) - (r_lg - r_rg)) / 2
                tol = max(GAP_TOL_PX, GAP_TOL_PCT / 100.0 * r_cw)
                rows.append(
                    {
                        "viewport": viewport,
                        "section": name,
                        "check": "group-asym",
                        "group": key,
                        "refLeftGap": round(r_lg, 1),
                        "refRightGap": round(r_rg, 1),
                        "implLeftGap": round(i_lg, 1),
                        "implRightGap": round(i_rg, 1),
                        "deltaPx": round(delta, 1),
                        "tolerancePx": round(tol, 1),
                        "status": "fail" if delta > tol else "ok",
                    }
                )

                # Per-child shift prong (batch-7 ITEM 3, hardened batch-8 ITEM 7):
                # the union envelope (min-left..max-right) is SYMMETRIC when a
                # painting sibling sits off-centre the opposite way from the real
                # content, and a MEDIAN per-child offset cancels the SAME way when
                # 3+ children are spread so their individual offsets average to
                # centre. Compare the sorted per-child offset DISTRIBUTION ref-vs-
                # impl (worst element-wise divergence over the common length), so
                # symmetric dispersion no longer cancels — any child off-centre
                # beyond tolerance fails even when the median and union read 0.
                r_centers = rg.get("childCenters")
                i_centers = ig.get("childCenters")
                r_cc = r_cl + r_cw / 2.0
                i_cc = i_cl + i_cw / 2.0
                r_offs = [_num(c) - r_cc for c in (r_centers or []) if _num(c) is not None] \
                    if isinstance(r_centers, list) else []
                i_offs = [_num(c) - i_cc for c in (i_centers or []) if _num(c) is not None] \
                    if isinstance(i_centers, list) else []
                if r_offs and i_offs:
                    r_sorted = sorted(r_offs)
                    i_sorted = sorted(i_offs)
                    pair_n = min(len(r_sorted), len(i_sorted))
                    cdelta = max(abs(i_sorted[k] - r_sorted[k]) for k in range(pair_n))
                    ctol = max(GAP_TOL_PX, GAP_TOL_PCT / 100.0 * (i_cw or 0))
                    rows.append(
                        {
                            "viewport": viewport,
                            "section": name,
                            "check": "group-childshift",
                            "group": key,
                            "refChildOffsetsPx": [round(o, 1) for o in r_sorted],
                            "implChildOffsetsPx": [round(o, 1) for o in i_sorted],
                            "deltaPx": round(cdelta, 1),
                            "tolerancePx": round(ctol, 1),
                            "status": "fail" if cdelta > ctol else "ok",
                            "reason": (
                                "per-child placement diverges from the ref: at "
                                "least one content child is off-centre beyond "
                                "tolerance while the union envelope (and the "
                                "median) read symmetric — symmetric dispersion no "
                                "longer cancels"
                            )
                            if cdelta > ctol
                            else None,
                        }
                    )

fail_rows = [r for r in rows if r["status"] == "fail"]
if fail_rows:
    status, code = "fail", 1
elif unmeasured:
    status, code = "warn", 0
elif rows:
    status, code = "pass", 0
else:
    status, code = "skip", 0

payload: dict[str, Any] = {
    "schemaVersion": 1,
    "status": status,
    "viewportsChecked": viewports_checked,
    "checkedRows": len(rows),
    "failCount": len(fail_rows),
    "rows": rows,
    "unmeasured": unmeasured,
    "rule": RULE,
}
if status == "skip" and not rows:
    payload["reason"] = "no matched section rows to evaluate"
if unmeasured:
    payload["remediation"] = REMEDIATION
if fail_rows:
    worst = max(fail_rows, key=lambda r: float(r.get("deltaPx") or 0.0))
    payload["diagnostic"] = (
        f"{len(fail_rows)} alignment failure(s); worst: {worst['section']} "
        f"@{worst['viewport']} {worst['check']} delta {worst.get('deltaPx')}px "
        f"(tolerance {worst.get('tolerancePx')}px). Inner content is horizontally "
        "mis-placed relative to the ref — look for viewport-specific pixel "
        "constants (e.g. left/right margins baked for one design width) in "
        "the section's implementation."
    )

write(payload, code)
