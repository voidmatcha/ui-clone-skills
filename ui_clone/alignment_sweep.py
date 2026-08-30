"""Alignment invariant transfer to intermediate viewport widths.

Plan viewports carry ref geometry (section-compare's per-viewport
matches.json with contentBox/contentGroups gaps — Item: alignment-parity).
Intermediate widths have NO ref geometry, so this module transfers
invariants instead of comparing absolutes:

  centered      — |leftGap - rightGap| <= max(12px, 1% basis) at ALL
                  enforced desktop viewports → the impl must stay centered
                  at every sweep width.
  fixed-gutter  — leftGap constant across enforced desktop viewports → the
                  impl must keep that gutter at every sweep width.
  overflow      — ref content exceeds its container → skipped because
                  equal negative gaps are not a transferable centering rule.
  proportional  — anything else → skipped (no transferable invariant).

Blocking policy: a violation must repeat at two ADJACENT sweep widths (or
hit one enforced plan width) before it blocks — a single mid-width wobble is
recorded as advisory, not failed. The sweep itself is IMPL-ONLY (one browser
session, DOM rects, no screenshots); ref truth enters solely through the
classification.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

DESKTOP_MIN_WIDTH = int(os.environ.get("UI_CLONE_ALIGN_DESKTOP_MIN_WIDTH", "768"))
DESKTOP_MAX_WIDTH = int(os.environ.get("UI_CLONE_ALIGN_DESKTOP_MAX_WIDTH", "1920"))
# Tolerances are env-tunable per repo convention; defaults preserve the
# calibrated values (the regression corpus). A LARGER tolerance is more
# lenient (a bypass surface — batch-7 ITEM 4), so each is clamped to a max that
# still admits sub-pixel AA / rounding but cannot be opened wide enough to pass
# a real off-center defect (the specific regression class is >=64px).


def _clamp_tol(env: str, default: str, ceiling: float) -> float:
    try:
        return min(ceiling, max(0.0, float(os.environ.get(env, default))))
    except (TypeError, ValueError):
        return float(default)


CENTER_TOL_PX = _clamp_tol("UI_CLONE_ALIGN_GAP_PX", "12", 32.0)
CENTER_TOL_FRAC = _clamp_tol("UI_CLONE_ALIGN_GAP_PCT", "1.0", 2.5) / 100.0
GUTTER_CONST_TOL_PX = _clamp_tol("UI_CLONE_ALIGN_GUTTER_CONST_PX", "8", 32.0)

_BP_RE = re.compile(r"^\s*([0-9.]+)\s*(px|rem|em)?\s*$")


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _gaps(row: dict[str, Any]) -> tuple[float, float] | None:
    lg, rg = _num(row.get("leftGap")), _num(row.get("rightGap"))
    if lg is not None and rg is not None:
        return lg, rg
    cb = row.get("contentBox")
    rect = row.get("rect")
    if not isinstance(cb, dict) or not isinstance(rect, dict):
        return None
    c_l, c_w = _num(cb.get("left")), _num(cb.get("width"))
    r_l, r_w = _num(rect.get("left")), _num(rect.get("width"))
    if c_l is None or c_w is None or r_l is None or r_w is None:
        return None
    return c_l - r_l, (r_l + r_w) - (c_l + c_w)


def _group_gaps(group: dict[str, Any]) -> tuple[float, float, float] | None:
    """(leftGap, rightGap, containerWidth) for a contentGroups entry."""
    c_l = _num(group.get("containerLeft"))
    c_w = _num(group.get("containerWidth"))
    u_l = _num(group.get("unionLeft"))
    u_w = _num(group.get("unionWidth"))
    if c_l is None or c_w is None or u_l is None or u_w is None:
        return None
    return u_l - c_l, (c_l + c_w) - (u_l + u_w), c_w


def _center_tol(basis_width: float) -> float:
    return max(CENTER_TOL_PX, CENTER_TOL_FRAC * basis_width)


def _viewport_width(viewport: str) -> int | None:
    try:
        return int(viewport.split("x")[0])
    except (ValueError, IndexError):
        return None


def _identity_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "section"


def _section_key(name: str, fingerprint: str, ambiguous_names: set[str]) -> str:
    if name in ambiguous_names and fingerprint:
        return f"{name}#{_identity_slug(fingerprint)}"
    return name


def _grouped_keys(row: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Per-group gap tuples keyed name[occurrence] (mirrors alignment-parity)."""
    out: dict[str, tuple[float, float, float]] = {}
    seen: dict[str, int] = {}
    for group in row.get("contentGroups") or []:
        if not isinstance(group, dict):
            continue
        base = str(group.get("name") or "group")
        n = seen.get(base, 0)
        seen[base] = n + 1
        gaps = _group_gaps(group)
        if gaps is not None:
            out[f"{base}[{n}]"] = gaps
    return out


def classify(
    ref_rows_by_viewport: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Classify sections + groups from ref data across enforced desktop viewports.

    Keys: section name, or "<section>::<group>[i]" for contentGroups.
    Kinds: centered | fixed-gutter | overflow | proportional | unclassifiable.
    A key must be measurable at every desktop viewport to be classified;
    missing gap fields anywhere make it unclassifiable (frozen-ref debt —
    surfaced, never silently treated as proportional).
    """
    # key -> list of (leftGap, rightGap, basisWidth) across viewports
    observed: dict[str, list[tuple[float, float, float]]] = {}
    missing: dict[str, bool] = {}
    impl_classes_by_name: dict[str, list[str]] = {}
    impl_fingerprints_by_name: dict[str, list[str]] = {}
    impl_indices_by_name: dict[str, list[int]] = {}
    desktop_count = 0
    fingerprints_by_name: dict[str, set[str]] = {}

    for viewport, matches in ref_rows_by_viewport.items():
        vp_w = _viewport_width(viewport)
        if vp_w is None or vp_w < DESKTOP_MIN_WIDTH:
            continue
        for match in matches or []:
            if not isinstance(match, dict):
                continue
            ref_row = match.get("ref")
            if not isinstance(ref_row, dict):
                continue
            impl_row = match.get("impl")
            if not isinstance(impl_row, dict):
                continue
            fingerprint = str(impl_row.get("fingerprint") or "").strip()
            if not fingerprint:
                continue
            name = str(match.get("name") or ref_row.get("className") or "section")
            fingerprints_by_name.setdefault(name, set()).add(fingerprint)
    ambiguous_names = {
        name for name, fingerprints in fingerprints_by_name.items() if len(fingerprints) > 1
    }

    for viewport, matches in ref_rows_by_viewport.items():
        vp_w = _viewport_width(viewport)
        if vp_w is None or vp_w < DESKTOP_MIN_WIDTH:
            continue
        desktop_count += 1
        for match in matches or []:
            if not isinstance(match, dict):
                continue
            ref_row = match.get("ref")
            if not isinstance(ref_row, dict):
                continue
            name = str(match.get("name") or ref_row.get("className") or "section")
            impl_row = match.get("impl")
            impl_fingerprint = ""
            if isinstance(impl_row, dict):
                impl_fingerprint = str(impl_row.get("fingerprint") or "").strip()
            key_name = _section_key(name, impl_fingerprint, ambiguous_names)
            rect_raw = ref_row.get("rect")
            rect = rect_raw if isinstance(rect_raw, dict) else {}
            basis = _num(rect.get("width")) or float(vp_w)
            gaps = _gaps(ref_row)
            if gaps is None:
                missing[key_name] = True
            else:
                observed.setdefault(key_name, []).append((gaps[0], gaps[1], basis))
            for key, (g_l, g_r, g_w) in _grouped_keys(ref_row).items():
                observed.setdefault(f"{key_name}::{key}", []).append((g_l, g_r, g_w))
            # The sweep enumerates the LIVE impl, whose rows carry impl class
            # names — record them so evaluation can pair "footer-2" (a
            # safe-section name) back to the live row.
            if isinstance(impl_row, dict):
                impl_cls = str(impl_row.get("className") or "").strip()
                if impl_cls:
                    impl_classes_by_name.setdefault(key_name, []).append(impl_cls)
                if impl_fingerprint:
                    impl_fingerprints_by_name.setdefault(key_name, []).append(
                        impl_fingerprint
                    )
                impl_index = impl_row.get("index")
                if isinstance(impl_index, int):
                    impl_indices_by_name.setdefault(key_name, []).append(impl_index)

    out: dict[str, dict[str, Any]] = {}
    for key, rows in observed.items():
        if missing.get(key) or len(rows) < desktop_count:
            out[key] = {"kind": "unclassifiable"}
            continue
        if not rows:
            out[key] = {"kind": "unclassifiable"}
            continue
        basis = min(w for _, _, w in rows)
        if any(lg + rg < -_center_tol(w) for lg, rg, w in rows):
            out[key] = {"kind": "overflow", "basisWidth": basis}
            continue
        if all(abs(lg - rg) <= _center_tol(w) for lg, rg, w in rows):
            out[key] = {"kind": "centered", "basisWidth": basis}
            continue
        gutters = [lg for lg, _, _ in rows]
        if max(gutters) - min(gutters) <= GUTTER_CONST_TOL_PX:
            out[key] = {
                "kind": "fixed-gutter",
                "refLeftGap": sum(gutters) / len(gutters),
                "basisWidth": basis,
            }
            continue
        out[key] = {"kind": "proportional"}
    for key in missing:
        if out.get(key, {}).get("kind") not in ("centered", "fixed-gutter"):
            out[key] = {"kind": "unclassifiable"}
    for key, info in out.items():
        section_key = key.split("::")[0]
        classes = impl_classes_by_name.get(section_key) or []
        if classes:
            info["implClassName"] = Counter(classes).most_common(1)[0][0]
        fingerprints = impl_fingerprints_by_name.get(section_key) or []
        if fingerprints:
            info["implFingerprint"] = Counter(fingerprints).most_common(1)[0][0]
        indices = impl_indices_by_name.get(section_key) or []
        if indices and len(set(indices)) == 1:
            info["implIndex"] = indices[0]
    return out


def sweep_widths(
    plan_widths: list[int],
    breakpoints: list[str],
    *,
    max_widths: int = 14,
) -> list[int]:
    """Midpoints between consecutive desktop plan widths + breakpoints ±1px,
    clamped to the plan range. A SINGLE-viewport plan is no longer sweep-exempt
    (batch-7 ITEM 3): when only one desktop width exists but breakpoints do, the
    sweep range widens to [DESKTOP_MIN_WIDTH .. max(width, DESKTOP_MAX_WIDTH)] so
    a viewport-specific defect at an impl @media boundary is still sampled."""
    desktop = sorted(w for w in plan_widths if w >= DESKTOP_MIN_WIDTH)
    widths: set[int] = set()
    if len(desktop) >= 2:
        lo, hi = desktop[0], desktop[-1]
        for a, b in zip(desktop, desktop[1:]):
            mid = (a + b) // 2
            if lo < mid < hi:
                widths.add(mid)
    elif desktop and breakpoints:
        only = desktop[0]
        lo, hi = min(only, DESKTOP_MIN_WIDTH), max(only, DESKTOP_MAX_WIDTH)
    else:
        return []
    for raw in breakpoints:
        m = _BP_RE.match(str(raw))
        if not m:
            continue
        value = float(m.group(1))
        unit = m.group(2) or "px"
        px = value * 16 if unit in ("rem", "em") else value
        for candidate in (int(px) - 1, int(px) + 1):
            if lo <= candidate <= hi and candidate not in desktop:
                widths.add(candidate)
    ordered = sorted(widths)
    if len(ordered) > max_widths:
        # keep extremes + evenly-spaced middle so adjacency stays meaningful
        step = (len(ordered) - 1) / (max_widths - 1)
        ordered = sorted({ordered[round(i * step)] for i in range(max_widths)})
    return ordered


def _row_for(
    key: str,
    sample_rows: list[dict[str, Any]],
    info: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pair a classification key to a live-impl enumeration row.

    Classification keys are safe-section names ("footer-2"); live rows carry
    impl class names — prefer the recorded implClassName, fall back to the
    section name itself (synthetic fixtures / id-named sections)."""
    candidates = []
    if info and info.get("implFingerprint"):
        target_fingerprint = str(info["implFingerprint"])
        target_class = str(info.get("implClassName") or "")
        if target_class:
            for row in sample_rows:
                row_class = str(row.get("className") or "")
                if (
                    str(row.get("fingerprint") or "") == target_fingerprint
                    and (
                        row_class == target_class
                        or target_class in row_class.split()
                        or target_class in row_class
                    )
                ):
                    return row
        else:
            for row in sample_rows:
                if str(row.get("fingerprint") or "") == target_fingerprint:
                    return row
        return None
    if info and isinstance(info.get("implIndex"), int):
        target_index = int(info["implIndex"])
        for row in sample_rows:
            if row.get("index") == target_index:
                return row
    if info and info.get("implClassName"):
        candidates.append(str(info["implClassName"]))
    candidates.append(key.split("::")[0])
    for target in candidates:
        for row in sample_rows:
            cls = str(row.get("className") or "")
            if cls == target or target in cls.split():
                return row
        for row in sample_rows:
            cls = str(row.get("className") or "")
            if target and target in cls:
                return row
    return None


def _measure(key: str, row: dict[str, Any]) -> tuple[float, float, float] | None:
    if "::" in key:
        group_key = key.split("::", 1)[1]
        return _grouped_keys(row).get(group_key)
    gaps = _gaps(row)
    if gaps is None:
        return None
    rect_raw = row.get("rect")
    rect = rect_raw if isinstance(rect_raw, dict) else {}
    basis = _num(rect.get("width")) or 0.0
    return gaps[0], gaps[1], basis


def evaluate(
    classification: dict[str, dict[str, Any]],
    samples: dict[int, list[dict[str, Any]]],
    *,
    plan_widths: list[int],
) -> tuple[list[dict[str, Any]], str]:
    """Evaluate impl sweep samples against transferred invariants.

    Row statuses: ok | violation | missing. A key BLOCKS (status fail) when
    it violates at two adjacent sweep widths or at any enforced plan width.
    Section-root violations stay advisory when a centered content group for
    that section is measured and passes at every sampled width.
    """
    rows: list[dict[str, Any]] = []
    widths = sorted(samples)
    enforced = set(plan_widths)
    violations: dict[str, list[int]] = {}

    for key, info in classification.items():
        kind = str(info.get("kind") or "")
        if kind not in ("centered", "fixed-gutter"):
            continue
        for width in widths:
            row = _row_for(key, samples[width] or [], info)
            if row is None:
                rows.append({"key": key, "width": width, "kind": kind, "status": "missing"})
                continue
            measured = _measure(key, row)
            if measured is None:
                rows.append({"key": key, "width": width, "kind": kind, "status": "missing"})
                continue
            lg, rg, basis = measured
            tol = _center_tol(basis or width)
            if kind == "centered":
                delta = abs(lg - rg) / 2
                ok = delta <= tol
            else:
                ref_gutter = float(info.get("refLeftGap") or 0.0)
                delta = abs(lg - ref_gutter)
                ok = delta <= tol
            rows.append(
                {
                    "key": key,
                    "width": width,
                    "kind": kind,
                    "leftGap": round(lg, 1),
                    "rightGap": round(rg, 1),
                    "deltaPx": round(delta, 1),
                    "tolerancePx": round(tol, 1),
                    "status": "ok" if ok else "violation",
                }
            )
            if not ok:
                violations.setdefault(key, []).append(width)

    passing_centered_sections: set[str] = set()
    for key, info in classification.items():
        if "::" not in key or info.get("kind") != "centered":
            continue
        section, group_occurrence = key.split("::", 1)
        group_name = group_occurrence.rsplit("[", 1)[0]
        root_info = classification.get(section, {})
        impl_class = str(root_info.get("implClassName") or "")
        root_aliases = {section, impl_class, *impl_class.split()}
        if root_info.get("kind") != "centered" or group_name not in root_aliases:
            continue
        group_rows = [row for row in rows if row["key"] == key]
        if group_rows and all(row["status"] == "ok" for row in group_rows):
            passing_centered_sections.add(section)

    blocking = False
    for key, bad_widths in violations.items():
        if "::" not in key and key in passing_centered_sections:
            continue
        if any(w in enforced for w in bad_widths):
            blocking = True
        bad = set(bad_widths)
        for a, b in zip(widths, widths[1:]):
            if a in bad and b in bad:
                blocking = True
    for row in rows:
        if row["status"] == "violation":
            if "::" not in row["key"] and row["key"] in passing_centered_sections:
                continue
            key_widths = set(violations.get(row["key"], []))
            adjacent = any(
                a in key_widths and b in key_widths for a, b in zip(widths, widths[1:])
            )
            if row["width"] in enforced or adjacent:
                row["status"] = "fail"

    if blocking:
        return rows, "fail"
    if any(r["status"] in ("violation", "missing") for r in rows):
        return rows, "warn"
    return rows, "pass" if rows else "skip"


# ── CLI ────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> Any:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _plan_viewports(ref_dir: Path) -> list[tuple[int, int]]:
    plan = _load_json(ref_dir / "verification-plan.json")
    out: list[tuple[int, int]] = []
    if isinstance(plan, dict):
        for vp in plan.get("viewports") or []:
            try:
                out.append((int(vp["w"]), int(vp["h"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _breakpoints(ref_dir: Path) -> list[str]:
    """Breakpoints to sweep around: the REF's @media boundaries PLUS the IMPL's
    own @media boundaries (batch-7 ITEM 3). A defect baked behind an @media the
    ref never had (proven 1440px window) is invisible unless the sweep samples
    the IMPL's breakpoints too; alignment-sweep-check.sh scans the impl CSS into
    impl-detected-breakpoints.json, which is merged here."""
    out: list[str] = []
    for name in ("detected-breakpoints.json", "impl-detected-breakpoints.json"):
        data = _load_json(ref_dir / name)
        if isinstance(data, dict) and isinstance(data.get("breakpoints"), list):
            out.extend(str(b) for b in data["breakpoints"])
    seen: set[str] = set()
    deduped: list[str] = []
    for b in out:
        if b not in seen:
            seen.add(b)
            deduped.append(b)
    return deduped


def _ref_rows_by_viewport(ref_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    vp_root = ref_dir / "sections" / "viewports"
    if vp_root.is_dir():
        for d in sorted(vp_root.iterdir()):
            rows = _load_json(d / "sections" / "matches.json")
            if isinstance(rows, list):
                out[d.name] = [r for r in rows if isinstance(r, dict)]
    return out


def nearest_height(width: int, plan_viewports: list[tuple[int, int]]) -> int:
    desktop = [(w, h) for w, h in plan_viewports if w >= DESKTOP_MIN_WIDTH]
    if not desktop:
        return 900
    return min(desktop, key=lambda wh: abs(wh[0] - width))[1]


def main(argv: list[str] | None = None) -> int:
    import json
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--emit-widths":
        if len(args) != 2:
            print("usage: alignment_sweep --emit-widths <ref-dir>", file=sys.stderr)
            return 2
        ref_dir = Path(args[1])
        vps = _plan_viewports(ref_dir)
        for width in sweep_widths([w for w, _ in vps], _breakpoints(ref_dir)):
            print(f"{width} {nearest_height(width, vps)}")
        return 0

    if len(args) != 2:
        print(
            "usage: alignment_sweep <ref-dir> <samples-file> | --emit-widths <ref-dir>",
            file=sys.stderr,
        )
        return 2
    ref_dir, samples_path = Path(args[0]), Path(args[1])
    classification = classify(_ref_rows_by_viewport(ref_dir))
    raw_samples = _load_json(samples_path)
    samples: dict[int, list[dict[str, Any]]] = {}
    if isinstance(raw_samples, dict):
        for k, v in raw_samples.items():
            try:
                samples[int(k)] = [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []
            except (TypeError, ValueError):
                continue

    vps = _plan_viewports(ref_dir)
    rows, status = evaluate(
        classification, samples, plan_widths=[w for w, _ in vps]
    )
    kinds = {k: v.get("kind") for k, v in classification.items()}
    unclassifiable = sorted(k for k, v in kinds.items() if v == "unclassifiable")
    transferable = [k for k, v in kinds.items() if v in ("centered", "fixed-gutter")]
    if status == "skip" and unclassifiable:
        # No transferable invariant AND frozen-ref debt — surface, never
        # silently pass.
        status = "warn"

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "status": status,
        "sweptWidths": sorted(samples),
        "classifiedKeys": kinds,
        "transferableCount": len(transferable),
        "unclassifiable": unclassifiable,
        "rows": rows,
        "rule": (
            "Sections/groups the ref keeps centered (or fixed-gutter) at every "
            "enforced desktop viewport must keep that invariant at intermediate "
            "widths; blocking requires two adjacent sweep-width violations or "
            "one enforced-width violation."
        ),
    }
    if unclassifiable:
        payload["remediation"] = (
            "ref recapture needed — contentBox/contentGroups fields absent on "
            "some ref rows (frozen artifacts predate the contentBox enumerator)"
        )
    fails = [r for r in rows if r["status"] == "fail"]
    if fails:
        worst = max(fails, key=lambda r: r.get("deltaPx", 0))
        payload["diagnostic"] = (
            f"{len(fails)} blocking alignment violation(s) at intermediate "
            f"widths; worst: {worst['key']} @{worst['width']}px delta "
            f"{worst.get('deltaPx')}px (tol {worst.get('tolerancePx')}px). "
            "Look for pixel constants baked for one design width."
        )
    (ref_dir / "alignment-sweep.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
