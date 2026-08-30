"""Live-parity census decisions — pure logic for live-parity-sweep.sh.

Three failure classes proved structurally noisy on timer/scroll-dynamic pages
(realfood loop-e2e-4 evidence capsule):

1. Image-file-count drift: custom setInterval carousels rotate the rendered
   food even while off-screen, and the pin hooks only reach swiper / slick /
   owl / embla — the census's instantaneous file counts then differ by carousel
   PHASE, not by missing assets. Rotation phase noise is bounded: the filename
   vocabulary matches on both sides and each per-file delta is at most 1.
   Anything beyond that (a file one side never renders, or a >1 instance gap)
   stays blocking.

2. scrollHeight exact equality: pages with scroll-activated content legally
   grow/shrink (realfood erf region +-180px), so equality across two
   independent sessions is luck. Tolerance is max(0.5%, 200px) — far below the
   2.2x balloon class (specific regression) this check exists to catch; geometry-sanity
   independently enforces docH within 15%.

3. Accessibility-only copy leaks: long text captured from clipped/screen-reader
   nodes or semantic metadata must remain non-visual. A structured palette once
   collapsed into a single text node, visibly dumping every AA/AAA label plus
   its long image description. Compare only long normalized strings, ignore any
   copy already visible in the reference, and block only when the implementation
   visibly renders the same string.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

SCROLLHEIGHT_PCT_TOLERANCE = 0.5
SCROLLHEIGHT_PX_FLOOR = 200.0
ACCESSIBILITY_COPY_MIN_CHARS = 40


def _normalize_copy(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def find_accessibility_text_leaks(
    candidates: Sequence[Mapping[str, object]],
    ref_visible_text: str,
    impl_visible_text: str,
) -> list[dict[str, str]]:
    """Return long accessibility-only strings rendered visibly only by impl.

    Candidate discovery belongs to the browser census, which limits rows to
    clipped/screen-reader text and long alt/ARIA descriptions. This pure layer
    applies the parity decision and deduplicates equivalent source strings.
    """
    ref_visible = _normalize_copy(ref_visible_text)
    impl_visible = _normalize_copy(impl_visible_text)
    leaks: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        text = str(candidate.get("text") or "").strip()
        normalized = _normalize_copy(text)
        if len(normalized) < ACCESSIBILITY_COPY_MIN_CHARS or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in ref_visible or normalized not in impl_visible:
            continue
        leaks.append(
            {
                "text": text[:500],
                "source": str(candidate.get("source") or ""),
                "tag": str(candidate.get("tag") or ""),
                "class": str(candidate.get("class") or "")[:160],
            }
        )
    return leaks


def classify_image_drift(
    ref_counts: dict[str, int],
    impl_counts: dict[str, int],
) -> str:
    """'clean' | 'advisory' (rotation phase noise) | 'blocking'."""
    if ref_counts == impl_counts:
        return "clean"
    if set(ref_counts) != set(impl_counts):
        return "blocking"
    for name in ref_counts:
        if abs(ref_counts[name] - impl_counts.get(name, 0)) > 1:
            return "blocking"
    return "advisory"


def scrollheight_within_tolerance(
    ref: float | None,
    impl: float | None,
) -> bool:
    if (not isinstance(ref, int) and not isinstance(ref, float)) or (
        not isinstance(impl, int) and not isinstance(impl, float)
    ):
        return False
    if ref <= 0:
        return False
    allowed = max(ref * SCROLLHEIGHT_PCT_TOLERANCE / 100.0, SCROLLHEIGHT_PX_FLOOR)
    return abs(float(impl) - float(ref)) <= allowed
