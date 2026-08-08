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


def _content_box_has_multiple_boxes(content_box: object) -> bool:
    if not isinstance(content_box, dict):
        return False
    try:
        return int(content_box.get("boxCount") or 0) > 1
    except (TypeError, ValueError):
        return False


def _near_black(stats: dict[str, float]) -> bool:
    return stats["mean"] < BLACK_MEAN_MAX and stats["std"] < BLACK_STD_MAX


def _flat(stats: dict[str, float]) -> bool:
    return stats["unique"] <= FLAT_MAX_COLORS and stats["dominant"] >= FLAT_DOMINANT_MIN


def guard_reason(
    ref: dict[str, float] | None,
    impl: dict[str, float] | None,
    *,
    content_bearing: bool,
    mask_pct: float,
) -> tuple[str | None, str]:
    """Return (reason, policy) for a crop pair; reason None means no guard.

    policy "all"       — convert any verdict (pass or fail) to UNMEASURED
    policy "pass-only" — convert only pass-tier verdicts; fails stay fails
    """
    if not content_bearing or ref is None or impl is None:
        return None, "pass-only"
    if ref["std"] < REF_MIN_STD:
        return (
            "blank-ref: ref crop std "
            f"{ref['std']} < {REF_MIN_STD} — capture failure, not impl evidence",
            "all",
        )
    if _near_black(ref) and _near_black(impl):
        return (
            "symmetric-blank: both crops near-black on a content-bearing "
            "section — absence of evidence, not a pass",
            "all",
        )
    if mask_pct > MASK_PCT_MAX:
        return (
            f"masked: {mask_pct:.1f}% of compared area is dynamic-masked — "
            "pixel verdict unmeasurable; rely on the masked-region motion "
            "proof for this section's dynamic content",
            "pass-only",
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
        reason, policy = guard_reason(
            ref_stats,
            impl_stats,
            content_bearing=content_bearing,
            mask_pct=mask_pct,
        )
        sections[name] = {
            "reason": reason,
            "policy": policy,
            "contentBearing": content_bearing,
            "maskPct": mask_pct,
            "ref": ref_stats,
            "impl": impl_stats,
        }
    return {
        "schemaVersion": 1,
        # Record the thresholds actually in force. Blank-ref detection now blocks
        # the gate, which gives an operator a reason to lower REF_MIN_STD until no
        # section is ever guarded — self-describing telemetry is what makes that
        # detectable after the fact.
        "thresholds": {"refMinStd": REF_MIN_STD},
        "rule": (
            "Content-bearing sections whose crops are blank, symmetric-black, "
            "majority-masked, or color-flattened cannot produce tier-ok pixel "
            "verdicts; such rows surface as UNMEASURED with the recorded "
            "telemetry instead of counting as pass evidence."
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
        f"{name}\t{info['reason']}\t{info['policy']}"
        for name, info in payload["sections"].items()
        if info.get("reason")
    ]
    (sections_dir / "crop-guards.tsv").write_text(
        "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
