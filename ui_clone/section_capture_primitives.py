"""Pure helpers for section capture: safe names, settle derivation, scroll
anchoring math, and ImageMagick crop/probe primitives.

Moved verbatim out of ``ui_clone.section_capture``; that module re-exports
every name here.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeGuard


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _is_number(value: object) -> TypeGuard[int | float]:
    """Keep shell entrypoints compatible with macOS system Python 3.9."""
    return isinstance(value, int) or isinstance(value, float)


def safe_section_name(raw: object, *, max_length: int = 80) -> str:
    """Return a filename-safe section name.

    Section names originate from reference DOM ids/classes. Treat them as
    untrusted display data: remove path traversal punctuation, shell metachars,
    whitespace, and quotes while preserving readable alphanumeric tokens.
    """
    text = str(raw or "")
    text = text.replace("\\", "_").replace("/", "_")
    text = _SAFE_NAME_RE.sub("_", text)
    text = _MULTI_UNDERSCORE_RE.sub("_", text).strip("._-")
    if not text:
        text = "section"
    return text[:max_length]


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _fmt_num(value: float) -> str:
    # Coerce first: callers may pass an int (e.g. forced_scroll_y) and
    # int.is_integer() only exists on Python 3.12+.
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _duration_to_seconds(dur: object) -> float | None:
    """Coerce a spec duration to seconds. Accepts a number (already seconds) or
    a CSS duration string ('1200ms', '1s', '0.8s') or a bare numeric string
    (seconds). Returns None when unparseable. transition-spec-extract emits
    ms/s strings while transition-spec-rules.md documents bare seconds — both
    must parse, or the derived settle silently falls back to the 0.5s floor and
    reference sections get captured mid-transition (codex P2 / extract H1)."""
    if isinstance(dur, bool):
        return None
    if _is_number(dur):
        return float(dur)
    if not isinstance(dur, str):
        return None
    s = dur.strip().lower()
    if not s:
        return None
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1])
        return float(s)  # bare numeric string -> seconds
    except ValueError:
        return None


def derive_settle_seconds(spec_path: Path | str) -> float:
    """H9 (loop-nvti-3/4): the fixed 0.5s settle captured choreography-alive
    reference pages MID-TRANSITION — transient ref crops overturned two
    eyeball observations before being identified. Derive the settle from the
    spec itself: rest-reeval margin (0.4s) + the longest declared transition
    duration, floor 0.5s, cap 4.0s. Absent/unparseable spec keeps the legacy
    0.5s (no behavior change for non-choreography sites, per the fable
    constraint that the value must be derived, never site-tuned)."""
    margin, floor, cap = 0.4, 0.5, 4.0
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return floor
    entries = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(entries, list):
        return floor
    longest = 0.0
    for e in entries:
        if not isinstance(e, dict):
            continue
        anim = e.get("animation")
        dur = anim.get("duration") if isinstance(anim, dict) else None
        if dur is None:
            continue
        secs = _duration_to_seconds(dur)
        if secs is None:
            continue
        longest = max(longest, secs)
    if longest <= 0.0:
        return floor
    return round(min(cap, max(floor, margin + longest)), 3)


def should_pin_to_bottom(
    *,
    top: float,
    height: float,
    scroll_height: float,
    viewport_h: float,
    factor: float = 1.5,
) -> bool:
    """True when the section's bottom sits within `factor` viewports of the
    page end.

    Near-end sections must be captured with the page pinned to maxScroll:
    (1) `window.scrollTo(top - 50)` silently clamps there anyway, so the
    legacy fixed `clip_top = 50` assumption cropped the wrong band, and
    (2) end-of-page reveal latches only mount content once the page is
    actually scrolled to the end (observed: a footer whose content never
    rendered inside the capture window, producing 2-color background-only
    crops on both sides and an AE=0 vacuous pass).

    A section whose own height already spans most of the document (a
    coarse single-section match on an impl without ref's granular markup)
    makes `top + height` land near `scroll_height` no matter where the
    section actually starts, so the heuristic above misfires and pins a
    whole-page section to maxScroll — scrolling past all real content into
    blank territory. Real footers/near-bottom elements never approach half
    the document height, so excluding that case only removes the
    degenerate whole-page-as-one-section match, not a legitimate near-end
    element. Tradeoff: on a short page (e.g. a 2-viewport landing page) a
    genuinely final, full-viewport-height section can legitimately reach
    this 50% threshold and lose pinning, cropping its last ~viewport_h/2 of
    content instead of the true bottom. Symmetric across ref/impl (both
    captured identically), so it doesn't corrupt the AE verdict — just a
    known imprecision on short pages, not addressed here.
    """
    if viewport_h <= 0:
        return False
    if scroll_height > 0 and height >= scroll_height * 0.5:
        return False
    return top + height >= scroll_height - factor * viewport_h


def desired_scroll_y(
    *,
    top: float,
    height: float,
    scroll_height: float,
    viewport_h: float,
    factor: float = 1.5,
) -> float:
    if should_pin_to_bottom(
        top=top, height=height, scroll_height=scroll_height,
        viewport_h=viewport_h, factor=factor,
    ):
        return max(0.0, scroll_height - viewport_h)
    return max(0.0, top - 50.0)


def crop_unique_colors(image_path: Path) -> int | None:
    proc = subprocess.run(
        ["magick", "identify", "-format", "%k", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _crop_is_blank(image_path: Path, *, min_std: float = 0.05) -> bool:
    """True when a crop carries no real content: an off-canvas 1x1 stub, or a
    near-uniform band (std below min_std) — the blank-ref capture-failure class
    a pinned tall section hits when maxScroll scrolls past its top content."""
    proc = subprocess.run(
        ["magick", "identify", "-format", "%w %h %[fx:standard_deviation]", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        parts = proc.stdout.strip().split()
        w, h, std = int(parts[0]), int(parts[1]), float(parts[2])
    except (ValueError, IndexError):
        return False
    if w <= 2 and h <= 2:
        return True
    return std < min_std


def crop_is_off_canvas(*, clip_top: float, crop_h: float, canvas_h: float) -> bool:
    """True when the crop rect has zero intersection with the screenshot.

    Off-canvas rects happen legitimately: a settled intro overlay parked at
    page rect -900..0 (end-to-end run). ImageMagick's out-of-bounds crop output
    then depends on the source PNG's alpha channel — transparent on the
    alpha-bearing ref capture, a clamped edge pixel on a no-alpha impl
    screenshot — which guarantees a saturating 1px AE diff that no impl
    change can fix.
    """
    return clip_top + crop_h <= 0 or clip_top >= canvas_h


def write_transparent_stub(image_path: Path) -> None:
    """Deterministic 1x1 fully-transparent RGBA crop for off-canvas rects."""
    subprocess.run(
        ["magick", "-size", "1x1", "xc:none", f"PNG32:{image_path}"],
        capture_output=True,
        text=True,
        check=False,
    )


def _canvas_height(image_path: Path) -> float:
    proc = subprocess.run(
        ["magick", "identify", "-format", "%h", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _rect_from_capture(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    rect = {
        "top": _as_float(raw.get("top")),
        "left": _as_float(raw.get("left")),
        "width": _as_float(raw.get("width")),
        "height": _as_float(raw.get("height")),
    }
    if rect["width"] <= 0 or rect["height"] <= 0:
        return None
    return rect
