"""Capture readiness — detect a DOM snapshot that missed rendered content.

Root cause (loop-claude-ebay): ``extract-dom.sh`` snapshots the reused
agent-browser session with no readiness gate. On a transient pre-settle / error
frame it can capture a ``structure.json`` missing the main content region, while
``extract-asset-metadata.sh`` — running later against the SAME session after it
settled — records the rendered images (``visibleImages``) with positions. The two
artifacts then disagree, and the scaffold is built from the impoverished DOM.

The orphan-image count is the only signal empirically shown to fire on this case
(46/48 on the eBay capture) and is fully site-agnostic: no brand strings, no
selectors. It counts positioned images the page rendered whose distinctive source
token is absent from the DOM snapshot. The orchestrator uses it to re-snapshot the
now-settled session (no reload — the grid is already in the session DOM, proven by
the images just recorded) and, if that still fails, to write an honest
``capture-readiness.json`` degraded marker instead of silently shipping the
impoverished DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ALNUM = re.compile(r"[A-Za-z0-9]+")

# A majority of cross-checkable images missing from the DOM snapshot means the
# snapshot caught a pre-settle / error frame. The absolute floor keeps a couple of
# legitimately-lazy/virtualized orphans on a thin page from tripping recovery.
_DEGRADED_RATIO = 0.5
_MIN_ORPHAN = 4
# Below this length a "token" is too generic (e.g. "g", "webp") to prove identity.
_MIN_TOKEN_LEN = 4


def _distinctive_token(src: str | None) -> str | None:
    """The most identifying token of an image URL: the longest alphanumeric run
    across its path segments (a CDN content hash such as eBay's
    ``/images/g/<hash>/``). Shared filenames (``s-l1600.webp``) are NOT distinctive
    across products, so a bare-filename match would false-match; the long hash is
    the load-bearing identifier. Returns None for data URIs and token-less srcs."""
    if not src or src.startswith("data:"):
        return None
    path = src.split("?", 1)[0].split("#", 1)[0]
    tokens: list[str] = []
    for seg in (s for s in path.split("/") if s):
        tokens.extend(_ALNUM.findall(seg))
    if not tokens:
        return None
    return max(tokens, key=len)


def orphan_image_count(structure: object, visible_images: list) -> tuple[int, int]:
    """Return ``(orphan, total)``.

    ``total`` counts ``visibleImages`` entries with a cross-checkable distinctive
    token; ``orphan`` counts those whose token is absent from the serialized DOM
    snapshot. Data-URI and token-less entries are excluded from ``total`` — they
    can neither confirm nor deny capture.
    """
    dom_blob = json.dumps(structure)
    orphan = 0
    total = 0
    for img in visible_images:
        if not isinstance(img, dict):
            continue
        token = _distinctive_token(img.get("src") or img.get("originalSrc") or "")
        if token is None or len(token) < _MIN_TOKEN_LEN:
            continue
        total += 1
        if token not in dom_blob:
            orphan += 1
    return orphan, total


def readiness_verdict(
    orphan: int,
    total: int,
    *,
    ratio_threshold: float = _DEGRADED_RATIO,
    min_orphan: int = _MIN_ORPHAN,
) -> dict:
    """Classify a capture. ``degraded`` when a majority (``>= ratio_threshold``) of
    cross-checkable rendered images are missing from the DOM snapshot AND at least
    ``min_orphan`` are missing in absolute terms — i.e. the snapshot caught a
    pre-settle / error frame. ``ok`` otherwise. ``needsResnapshot`` mirrors the
    degraded decision so the orchestrator can drive a bounded re-snapshot loop."""
    ratio = (orphan / total) if total else 0.0
    degraded = total > 0 and orphan >= min_orphan and ratio >= ratio_threshold
    return {
        "status": "degraded" if degraded else "ok",
        "orphanImages": orphan,
        "checkableImages": total,
        "orphanRatio": round(ratio, 4),
        "needsResnapshot": degraded,
    }


def _unknown() -> dict:
    """The verdict when we cannot cross-check (a required artifact is
    missing/unreadable). ``needsResnapshot`` is False so the orchestrator never
    loops blindly."""
    return {
        "status": "unknown",
        "orphanImages": 0,
        "checkableImages": 0,
        "orphanRatio": 0.0,
        "needsResnapshot": False,
    }


# The rendered-image list lives in different artifacts at different pipeline
# points. At the Phase 2.5 recovery consult point ``visible-images.json`` (produced
# by extract-asset-metadata, key ``images``) exists, while ``extracted.json`` is
# assembled ~45s later at Step 6b (key ``visibleImages``). Try the early artifact
# first so the readiness check actually runs during recovery, then fall back to the
# assembled one for post-hoc callers.
_VISIBLE_IMAGE_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("visible-images.json", ("images",)),
    ("extracted.json", ("visibleImages", "visible_images")),
)


def _load_visible_images(ref: Path) -> list | None:
    """Return the rendered-image list from the first readable source artifact, or
    None when none exists (cannot cross-check)."""
    for name, keys in _VISIBLE_IMAGE_SOURCES:
        try:
            data = json.loads((ref / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def score_capture(ref_dir: str | Path) -> dict:
    """Load ``structure.json`` + the rendered-image list from ``ref_dir`` and return
    the readiness verdict. ``structure.json`` is required; the rendered images come
    from ``visible-images.json`` (present at Phase 2.5) or, failing that,
    ``extracted.json`` (present post-assembly). A missing/unreadable required
    artifact yields ``status: unknown`` with ``needsResnapshot: False`` so the
    orchestrator does not loop blindly."""
    ref = Path(ref_dir)
    try:
        structure = json.loads((ref / "structure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _unknown()
    vi = _load_visible_images(ref)
    if vi is None:
        return _unknown()
    orphan, total = orphan_image_count(structure, vi)
    return readiness_verdict(orphan, total)
