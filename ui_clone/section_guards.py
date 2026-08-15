"""Crop-evidence guards against vacuous section-compare passes.

Loop-9 regression class: a near-bottom footer section's reveal never mounted
inside the capture window, so BOTH crops contained only background rectangles
(2 unique colors), AE scored 0, and the row passed "ok" over zero actual
content — while mask telemetry (mask-elements.json [] / mask-coverage 0.0)
said nothing was masked.

This module computes per-crop truth and emits per-section unmeasured reasons
that section-compare.sh's AE loop uses to convert vacuous passes into
explicit UNMEASURED rows:

  blank-ref       — ref crop std < SECTION_REF_MIN_STD on a content-bearing
                    section. Applies to ALL verdict tiers (policy "all"): a
                    blank reference crop is a capture failure, so neither a
                    pass nor a fail against it is impl evidence.
  symmetric-blank — both crops near-black on a content-bearing section
                    (policy "all"; the old guard only failed impl-black vs
                    ref-content, silently passing black-vs-black).
  masked          — >60% of the compared area is dynamic-masked
                    (policy "pass-only": a fail on the unmasked remainder is
                    still real evidence). Pointer: the masked content is
                    verified by the masked-region motion proof, not pixels.
  fully-masked-media-only
                  — >=99.5% deliberately masked media with high-confidence,
                    matching ref/impl structure and plan-declared block gates
                    for media presence, live playback, motion parity, and
                    structural parity
                    (policy "structural-only"). Anything less remains
                    UNMEASURED rather than weakening pixel evidence.
  flat            — both crops quantize to <= UI_CLONE_FLAT_MAX_COLORS unique
                    colors with a dominant color covering >= 60% on a
                    content-bearing section (policy "pass-only"). Rendered
                    text/images always produce many levels via antialiasing;
                    a flat pair means the content never rendered in the
                    capture window.

CLI:
    python -m ui_clone.section_guards <sections-dir>

Reads  <sections-dir>/{matches.json, mask-coverage.json, ref/*.png, impl/*.png}
Writes <sections-dir>/crop-guards.json  (full telemetry — true flatness/mask)
       <sections-dir>/crop-guards.tsv   (name \t reason \t policy, guard rows only)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .section_capture import safe_section_name

REF_MIN_STD = float(os.environ.get("SECTION_REF_MIN_STD", "0.05"))
BLACK_MEAN_MAX = float(os.environ.get("UI_CLONE_BLACK_MEAN_MAX", "0.06"))
BLACK_STD_MAX = float(os.environ.get("UI_CLONE_BLACK_STD_MAX", "0.06"))
MASK_PCT_MAX = float(os.environ.get("UI_CLONE_MASK_PCT_MAX", "60"))
FLAT_MAX_COLORS = int(os.environ.get("UI_CLONE_FLAT_MAX_COLORS", "8"))
FLAT_DOMINANT_MIN = float(os.environ.get("UI_CLONE_FLAT_DOMINANT_MIN", "0.6"))
SPARSE_DETAIL_MIN_COLORS = int(os.environ.get("UI_CLONE_SPARSE_DETAIL_MIN_COLORS", "32"))
SPARSE_DETAIL_DOMINANT_MAX = float(os.environ.get("UI_CLONE_SPARSE_DETAIL_DOMINANT_MAX", "0.985"))
# These thresholds authorize a pixel-evidence deferral, so unlike diagnostic
# thresholds they are intentionally not operator-tunable. Lowering them would
# turn a fail-closed exception into a generic mask bypass.
FULLY_MASKED_MEDIA_MIN_PCT = 99.5
FULLY_MASKED_MEDIA_MIN_SCORE = 0.98
GEOM_REL_TOL = 0.01
GEOM_ABS_TOL = 2.0
LIVE_MEDIA_PROOF_BY_MASK_KIND = {
    "video": "video-play-proof",
    "canvas": "runtime-frame-proof",
}
MASKED_MEDIA_MOTION_PROOF = "video-motion-compare"
STRUCTURAL_ANCHOR_CHECKS = {
    "runtime-dom-parity",
    "hero-composite-check",
    "geometry-sanity",
    "live-parity-sweep",
}


def crop_stats(png_path: Path | str) -> dict[str, float] | None:
    """Grayscale mean/std (0..1), unique level count, dominant-level fraction."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(png_path) as im:
            gray = im.convert("L")
            # Downsample very large crops for speed; flatness/dominance are
            # scale-stable properties.
            if gray.width * gray.height > 4_000_000:
                gray = gray.reduce(2)
            # Histogram avoids materializing per-pixel data (and Pillow's
            # getdata deprecation); index == gray level for mode "L".
            hist = gray.histogram()
    except (OSError, ValueError):
        return None
    n = sum(hist)
    if n == 0:
        return None
    counts = {level: c for level, c in enumerate(hist) if c > 0}
    mean = sum(v * c for v, c in counts.items()) / n / 255.0
    var = sum(((v / 255.0 - mean) ** 2) * c for v, c in counts.items()) / n
    return {
        "mean": round(mean, 4),
        "std": round(var**0.5, 4),
        "unique": float(len(counts)),
        "dominant": round(max(counts.values()) / n, 4),
    }


def is_content_bearing(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    text = str(row.get("textWords") or row.get("fingerprint") or "").strip()
    try:
        children = int(row.get("childCount") or 0)
    except (TypeError, ValueError):
        children = 0
    groups = row.get("contentGroups")
    has_groups = isinstance(groups, list) and bool(groups)
    return (
        bool(text)
        or row.get("hasSvgText") is True
        or has_groups
        or _has_visible_media(row)
        or children > 1
        or _content_box_has_multiple_boxes(row.get("contentBox"))
    )


def _has_visible_media(row: dict[str, Any]) -> bool:
    for key in (
        "hasMedia",
        "hasVisibleMedia",
        "visibleMedia",
    ):
        if row.get(key) is True:
            return True
    for key in (
        "mediaCount",
        "visibleMediaCount",
        "imageCount",
        "videoCount",
        "canvasCount",
        "iframeCount",
        "pictureCount",
    ):
        try:
            if int(row.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _visible_media_count(row: dict[str, Any]) -> int:
    total = 0
    for key in ("visibleMediaCount", "mediaCount"):
        try:
            value = int(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    for key in ("imageCount", "videoCount", "canvasCount", "iframeCount", "pictureCount"):
        try:
            total += int(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
    if total > 0:
        return total
    for key in ("hasMedia", "hasVisibleMedia", "visibleMedia"):
        if row.get(key) is True:
            return 1
    return 0


def _visible_media_kinds(row: dict[str, Any]) -> list[str]:
    kinds = row.get("visibleMediaKinds")
    if not isinstance(kinds, list):
        return []
    return sorted(
        {str(kind).strip().lower() for kind in kinds if isinstance(kind, str) and str(kind).strip()}
    )


def _visible_media_kind_counts(row: dict[str, Any]) -> dict[str, int]:
    raw = row.get("visibleMediaKindCounts")
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for raw_kind, raw_count in raw.items():
        if not isinstance(raw_kind, str):
            continue
        kind = raw_kind.strip().lower()
        if not kind:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[kind] = count
    return dict(sorted(counts.items()))


def _format_media_kind_counts(counts: dict[str, int]) -> str:
    return ",".join(f"{kind}:{count}" for kind, count in sorted(counts.items()))


def _content_box_has_multiple_boxes(content_box: object) -> bool:
    if not isinstance(content_box, dict):
        return False
    try:
        return int(content_box.get("boxCount") or 0) > 1
    except (TypeError, ValueError):
        return False


def _text_signal(row: dict[str, Any]) -> bool:
    return bool(str(row.get("textWords") or row.get("fingerprint") or "").strip())


def _has_content_groups(row: dict[str, Any]) -> bool:
    groups = row.get("contentGroups")
    return isinstance(groups, list) and bool(groups)


def _is_media_only_pair(ref_row: dict[str, Any], impl_row: dict[str, Any]) -> tuple[bool, str, int]:
    ref_media = _visible_media_count(ref_row)
    impl_media = _visible_media_count(impl_row)
    if ref_media <= 0 or impl_media <= 0:
        return False, "visible media missing", ref_media
    if ref_media != impl_media:
        return False, f"visible media count mismatch ({ref_media} != {impl_media})", ref_media
    ref_kinds = _visible_media_kinds(ref_row)
    impl_kinds = _visible_media_kinds(impl_row)
    if not ref_kinds or not impl_kinds:
        return False, "visible media kinds missing", ref_media
    if ref_kinds != impl_kinds:
        return (
            False,
            f"visible media kind mismatch ({','.join(ref_kinds)} != {','.join(impl_kinds)})",
            ref_media,
        )
    ref_kind_counts = _visible_media_kind_counts(ref_row)
    impl_kind_counts = _visible_media_kind_counts(impl_row)
    if not ref_kind_counts or not impl_kind_counts:
        return False, "visible media kind counts missing", ref_media
    if sorted(ref_kind_counts) != ref_kinds:
        return False, "ref visible media kinds/counts disagree", ref_media
    if sorted(impl_kind_counts) != impl_kinds:
        return False, "impl visible media kinds/counts disagree", ref_media
    if sum(ref_kind_counts.values()) != ref_media:
        return False, "ref visible media kind counts total mismatch", ref_media
    if sum(impl_kind_counts.values()) != impl_media:
        return False, "impl visible media kind counts total mismatch", ref_media
    if ref_kind_counts != impl_kind_counts:
        return (
            False,
            "visible media kind counts mismatch "
            f"({_format_media_kind_counts(ref_kind_counts)} != "
            f"{_format_media_kind_counts(impl_kind_counts)})",
            ref_media,
        )
    for side, row in (("ref", ref_row), ("impl", impl_row)):
        if _text_signal(row):
            return False, f"{side} has text/fingerprint", ref_media
        if row.get("hasSvgText") is True:
            return False, f"{side} has SVG text", ref_media
        if _has_content_groups(row):
            return False, f"{side} has content groups", ref_media
    return True, "ok", ref_media


def _same_token(ref_row: dict[str, Any], impl_row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    ref_value = next((ref_row.get(key) for key in keys if ref_row.get(key) is not None), None)
    impl_value = next((impl_row.get(key) for key in keys if impl_row.get(key) is not None), None)
    if ref_value is None or impl_value is None:
        return False
    return str(ref_value).lower() == str(impl_value).lower()


def _int_value(row: dict[Any, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _numbers_match(ref_value: object, impl_value: object) -> bool:
    numeric_value_types = (int, float, str)
    if not isinstance(ref_value, numeric_value_types) or not isinstance(
        impl_value, numeric_value_types
    ):
        return False
    try:
        ref_num = float(ref_value)
        impl_num = float(impl_value)
    except (TypeError, ValueError):
        return False
    return abs(ref_num - impl_num) <= max(GEOM_ABS_TOL, abs(ref_num) * GEOM_REL_TOL)


def _box_matches(ref_box: object, impl_box: object) -> bool:
    if not isinstance(ref_box, dict) or not isinstance(impl_box, dict):
        return False
    numeric_keys = sorted(
        {
            key
            for key in set(ref_box) | set(impl_box)
            if key in {"x", "y", "left", "top", "right", "bottom", "width", "height"}
        }
    )
    for key in numeric_keys:
        if not _numbers_match(ref_box.get(key), impl_box.get(key)):
            return False
    for key in ("boxCount", "childCount"):
        if key in ref_box or key in impl_box:
            if _int_value(ref_box, key) != _int_value(impl_box, key):
                return False
    return True


def _structural_geometry_matches(
    ref_row: dict[str, Any], impl_row: dict[str, Any]
) -> tuple[bool, str]:
    if not _same_token(ref_row, impl_row, ("tag", "tagName", "nodeName")):
        return False, "tag mismatch"
    if not _same_token(ref_row, impl_row, ("display",)):
        return False, "display mismatch"
    ref_children = _int_value(ref_row, "childCount")
    impl_children = _int_value(impl_row, "childCount")
    if ref_children is None or impl_children is None or ref_children != impl_children:
        return False, "child count mismatch"
    if not _box_matches(ref_row.get("rect"), impl_row.get("rect")):
        return False, "rect mismatch"
    if not _box_matches(ref_row.get("contentBox"), impl_row.get("contentBox")):
        return False, "contentBox mismatch"
    return True, "ok"


def _find_verification_plan(sections_dir: Path) -> Path | None:
    local = sections_dir / "verification-plan.json"
    if local.is_file():
        return local

    # Canonical layouts are either <ref>/sections or
    # <ref>/sections/viewports/<WxH>/sections. Resolve only those exact roots;
    # an unbounded ancestor walk can bind a different component's plan and use
    # unrelated live-media checks to authorize a pixel-evidence deferral.
    ref_root: Path | None = None
    if sections_dir.name == "sections":
        parent = sections_dir.parent
        if parent.parent.name == "viewports" and parent.parent.parent.name == "sections":
            ref_root = parent.parent.parent.parent
        else:
            ref_root = parent
    if ref_root is not None:
        candidate = ref_root / "verification-plan.json"
        if candidate.is_file():
            return candidate
    return None


def _load_mask_elements(sections_dir: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((sections_dir / "mask-elements.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _rect_bounds(box: object) -> tuple[float, float, float, float] | None:
    if not isinstance(box, dict):
        return None
    left_value = box.get("left", box.get("x"))
    top_value = box.get("top", box.get("y"))
    width_value = box.get("width")
    height_value = box.get("height")
    if left_value is None or top_value is None or width_value is None or height_value is None:
        return None
    try:
        left = float(left_value)
        top = float(top_value)
        width = float(width_value)
        height = float(height_value)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, left + width, top + height


def _rect_union_area(rects: list[tuple[float, float, float, float]]) -> float:
    if not rects:
        return 0.0
    x_edges = sorted({edge for left, _, right, _ in rects for edge in (left, right)})
    area = 0.0
    for x_left, x_right in zip(x_edges, x_edges[1:]):
        if x_right <= x_left:
            continue
        intervals = sorted(
            (top, bottom) for left, top, right, bottom in rects if left < x_right and right > x_left
        )
        covered_y = 0.0
        current_top: float | None = None
        current_bottom: float | None = None
        for top, bottom in intervals:
            if current_top is None or current_bottom is None:
                current_top, current_bottom = top, bottom
            elif top > current_bottom:
                covered_y += current_bottom - current_top
                current_top, current_bottom = top, bottom
            else:
                current_bottom = max(current_bottom, bottom)
        if current_top is not None and current_bottom is not None:
            covered_y += current_bottom - current_top
        area += (x_right - x_left) * covered_y
    return area


def _recognized_media_mask(
    ref_row: dict[str, Any], mask_elements: list[dict[str, Any]]
) -> tuple[list[str], float]:
    section = _rect_bounds(ref_row.get("rect"))
    if section is None:
        return [], 0.0
    section_left, section_top, section_right, section_bottom = section
    section_area = (section_right - section_left) * (section_bottom - section_top)
    clipped_rects: list[tuple[float, float, float, float]] = []
    kinds: set[str] = set()
    for element in mask_elements:
        kind = str(element.get("tag") or "").lower()
        if kind not in LIVE_MEDIA_PROOF_BY_MASK_KIND:
            continue
        bounds = _rect_bounds(element)
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        clipped = (
            max(section_left, left),
            max(section_top, top),
            min(section_right, right),
            min(section_bottom, bottom),
        )
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            continue
        kinds.add(kind)
        clipped_rects.append(clipped)
    coverage = 100.0 * _rect_union_area(clipped_rects) / section_area
    return sorted(kinds), round(min(100.0, coverage), 4)


def _declared_block_check_ids(sections_dir: Path) -> set[str]:
    plan_path = _find_verification_plan(sections_dir)
    if plan_path is None:
        return set()
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return set()
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("severity") != "block":
            continue
        check_id = row.get("id") or row.get("name")
        if isinstance(check_id, str) and check_id:
            ids.add(check_id)
    return ids


def _fully_masked_media_policy(
    match: dict[str, Any],
    ref_row: dict[str, Any],
    impl_row: dict[str, Any],
    *,
    mask_pct: float,
    masked_media_kinds: list[str],
    recognized_media_mask_pct: float,
    plan_check_ids: set[str],
) -> tuple[str | None, dict[str, Any]]:
    telemetry: dict[str, Any] = {
        "eligible": False,
        "maskPct": mask_pct,
        "minMaskPct": FULLY_MASKED_MEDIA_MIN_PCT,
        "matchScore": match.get("score"),
        "declaredBlockChecks": sorted(plan_check_ids),
        "maskedMediaKinds": masked_media_kinds,
        "recognizedMediaMaskPct": recognized_media_mask_pct,
    }
    if mask_pct < FULLY_MASKED_MEDIA_MIN_PCT:
        telemetry["rejectReason"] = "mask coverage below threshold"
        return None, telemetry
    try:
        score = float(match.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score < FULLY_MASKED_MEDIA_MIN_SCORE:
        telemetry["rejectReason"] = "match score below threshold"
        return None, telemetry
    media_ok, media_reason, media_count = _is_media_only_pair(ref_row, impl_row)
    telemetry["visibleMediaCount"] = media_count
    if not media_ok:
        telemetry["rejectReason"] = media_reason
        return None, telemetry
    geometry_ok, geometry_reason = _structural_geometry_matches(ref_row, impl_row)
    if not geometry_ok:
        telemetry["rejectReason"] = geometry_reason
        return None, telemetry
    if recognized_media_mask_pct < FULLY_MASKED_MEDIA_MIN_PCT or not masked_media_kinds:
        telemetry["rejectReason"] = "recognized media mask does not fully cover section"
        return None, telemetry
    ref_visible_kinds = _visible_media_kinds(ref_row)
    if sorted(masked_media_kinds) != ref_visible_kinds:
        telemetry["rejectReason"] = (
            "masked media kinds do not cover all visible media kinds "
            f"({','.join(sorted(masked_media_kinds))} != {','.join(ref_visible_kinds)})"
        )
        return None, telemetry
    required_live_proofs = sorted(
        {LIVE_MEDIA_PROOF_BY_MASK_KIND[kind] for kind in masked_media_kinds}
    )
    telemetry["requiredLiveMediaProofs"] = required_live_proofs
    structural_anchors = sorted(plan_check_ids & STRUCTURAL_ANCHOR_CHECKS)
    telemetry["declaredStructuralAnchors"] = structural_anchors
    if "required-media-coverage" not in plan_check_ids:
        telemetry["rejectReason"] = "required-media-coverage not declared as block"
        return None, telemetry
    missing_live_proofs = [proof for proof in required_live_proofs if proof not in plan_check_ids]
    if missing_live_proofs:
        missing = missing_live_proofs[0]
        kind = next(
            kind for kind in masked_media_kinds if LIVE_MEDIA_PROOF_BY_MASK_KIND[kind] == missing
        )
        telemetry["rejectReason"] = f"{missing} not declared as block for {kind} mask"
        return None, telemetry
    telemetry["requiredMotionProof"] = MASKED_MEDIA_MOTION_PROOF
    if MASKED_MEDIA_MOTION_PROOF not in plan_check_ids:
        telemetry["rejectReason"] = (
            f"{MASKED_MEDIA_MOTION_PROOF} not declared as block for masked media motion"
        )
        return None, telemetry
    if not structural_anchors:
        telemetry["rejectReason"] = "structural anchor not declared as block"
        return None, telemetry
    telemetry["eligible"] = True
    reason = (
        "fully-masked-media-only: "
        f"{mask_pct:.1f}% dynamic-masked media-only pair structurally matched "
        f"(score {score:.3f}); pixel verdict deferred to declared live media "
        f"proof {', '.join(required_live_proofs)} and motion proof "
        f"{MASKED_MEDIA_MOTION_PROOF}"
    )
    return reason, telemetry


def _near_black(stats: dict[str, float]) -> bool:
    return stats["mean"] < BLACK_MEAN_MAX and stats["std"] < BLACK_STD_MAX


def _flat(stats: dict[str, float]) -> bool:
    return stats["unique"] <= FLAT_MAX_COLORS and stats["dominant"] >= FLAT_DOMINANT_MIN


def _signal_rich(stats: dict[str, float]) -> bool:
    return stats["unique"] > FLAT_MAX_COLORS and stats["dominant"] < FLAT_DOMINANT_MIN


def _sparse_detail(stats: dict[str, float]) -> bool:
    return (
        stats["unique"] >= SPARSE_DETAIL_MIN_COLORS
        and stats["dominant"] < SPARSE_DETAIL_DOMINANT_MAX
    )


def guard_reason(
    ref: dict[str, float] | None,
    impl: dict[str, float] | None,
    *,
    content_bearing: bool,
    media_bearing: bool = False,
    mask_pct: float,
) -> tuple[str | None, str]:
    """Return (reason, policy) for a crop pair; reason None means no guard.

    policy "all"       — convert any verdict (pass or fail) to UNMEASURED
    policy "pass-only" — convert only pass-tier verdicts; fails stay fails
    """
    if not content_bearing or ref is None or impl is None:
        return None, "pass-only"
    signal_rich_media = media_bearing and _signal_rich(ref) and _signal_rich(impl)
    if mask_pct > MASK_PCT_MAX:
        return (
            f"masked: {mask_pct:.1f}% of compared area is dynamic-masked — "
            "pixel verdict unmeasurable; rely on the masked-region motion "
            "proof for this section's dynamic content",
            "pass-only",
        )
    if ref["std"] < REF_MIN_STD and not signal_rich_media and not _sparse_detail(ref):
        return (
            "blank-ref: ref crop std "
            f"{ref['std']} < {REF_MIN_STD} — capture failure, not impl evidence",
            "all",
        )
    if _near_black(ref) and _near_black(impl) and not signal_rich_media:
        return (
            "symmetric-blank: both crops near-black on a content-bearing "
            "section — absence of evidence, not a pass",
            "all",
        )
    if _flat(ref) and _flat(impl):
        return (
            f"flat: both crops quantize to <={FLAT_MAX_COLORS} colors "
            f"(ref {int(ref['unique'])}, impl {int(impl['unique'])}) with a "
            "dominant color >=60% — content never rendered in the capture "
            "window (bottom-anchored recapture / motion proof required)",
            "pass-only",
        )
    return None, "pass-only"


def evaluate_sections_dir(sections_dir: Path) -> dict[str, Any]:
    matches_path = sections_dir / "matches.json"
    try:
        matches = json.loads(matches_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        matches = []
    if not isinstance(matches, list):
        matches = []

    try:
        coverage = json.loads((sections_dir / "mask-coverage.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        coverage = {}
    if not isinstance(coverage, dict):
        coverage = {}

    sections: dict[str, Any] = {}
    plan_check_ids = _declared_block_check_ids(sections_dir)
    mask_elements = _load_mask_elements(sections_dir)
    for match in matches:
        if not isinstance(match, dict):
            continue
        name = safe_section_name(match.get("name"))
        ref_row = match.get("ref")
        impl_row = match.get("impl")
        if not isinstance(ref_row, dict) or not isinstance(impl_row, dict):
            continue
        ref_png = sections_dir / "ref" / f"{name}.png"
        impl_png = sections_dir / "impl" / f"{name}.png"
        if not ref_png.is_file() or not impl_png.is_file():
            continue
        ref_stats = crop_stats(ref_png)
        impl_stats = crop_stats(impl_png)
        try:
            mask_pct = float(coverage.get(name) or coverage.get(str(match.get("name"))) or 0.0)
        except (TypeError, ValueError):
            mask_pct = 0.0
        content_bearing = is_content_bearing(ref_row)
        media_bearing = _has_visible_media(ref_row)
        source = None
        masked_media_kinds, recognized_media_mask_pct = _recognized_media_mask(
            ref_row, mask_elements
        )
        structural_reason, structural_telemetry = _fully_masked_media_policy(
            match,
            ref_row,
            impl_row,
            mask_pct=mask_pct,
            masked_media_kinds=masked_media_kinds,
            recognized_media_mask_pct=recognized_media_mask_pct,
            plan_check_ids=plan_check_ids,
        )
        reason: str | None
        if structural_reason is not None:
            reason, policy = structural_reason, "structural-only"
            source = "masked-media-motion"
        else:
            guard_reason_text, policy = guard_reason(
                ref_stats,
                impl_stats,
                content_bearing=content_bearing,
                media_bearing=media_bearing,
                mask_pct=mask_pct,
            )
            reason = guard_reason_text
        sections[name] = {
            "reason": reason,
            "policy": policy,
            "source": source,
            "contentBearing": content_bearing,
            "mediaBearing": media_bearing,
            "maskPct": mask_pct,
            "ref": ref_stats,
            "impl": impl_stats,
            "maskedMediaOnly": structural_telemetry,
        }
    return {
        "schemaVersion": 1,
        # Record the thresholds actually in force. Blank-ref detection now blocks
        # the gate, which gives an operator a reason to lower REF_MIN_STD until no
        # section is ever guarded — self-describing telemetry is what makes that
        # detectable after the fact.
        "thresholds": {
            "refMinStd": REF_MIN_STD,
            "fullyMaskedMediaMinPct": FULLY_MASKED_MEDIA_MIN_PCT,
            "fullyMaskedMediaMinScore": FULLY_MASKED_MEDIA_MIN_SCORE,
            "geometryRelativeTolerance": GEOM_REL_TOL,
            "geometryAbsoluteTolerancePx": GEOM_ABS_TOL,
        },
        "rule": (
            "Content-bearing sections whose crops are blank, symmetric-black, "
            "majority-masked, or color-flattened cannot produce tier-ok pixel "
            "verdicts. They surface as UNMEASURED unless a >=99.5% masked, "
            "media-only ref/impl pair has high-confidence structural parity "
            "and plan-declared block gates for media presence, live playback, "
            "motion parity, and structure; only that narrow case becomes "
            "STRUCTURAL_ONLY."
        ),
        "sections": sections,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m ui_clone.section_guards <sections-dir>", file=sys.stderr)
        return 2
    sections_dir = Path(args[0])
    if not sections_dir.is_dir():
        print(f"sections dir not found: {sections_dir}", file=sys.stderr)
        return 2

    payload = evaluate_sections_dir(sections_dir)
    (sections_dir / "crop-guards.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    rows = [
        f"{name}\t{info['reason']}\t{info['policy']}\t{info.get('source') or ''}"
        for name, info in payload["sections"].items()
        if info.get("reason")
    ]
    (sections_dir / "crop-guards.tsv").write_text(
        "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
