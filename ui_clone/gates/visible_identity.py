"""Shared visible-identity + settle primitive (ITEM 0).

The single "resolve the rendered-visible target" helper used by every probe
gate. Given per-element records (emitted by the JS collector
skills/visual-debug/scripts/lib/visible-identity.js), it answers three
questions with one set of thresholds:

  1. is this element actually PAINTED to the user? (not display:none /
     visibility:hidden / opacity:0 / zero-area / transparent text /
     font-size:0 / off-viewport)
  2. among the matches for a selector, is the visible cardinality what the
     ref expects? (>expected visible => ambiguous-fail, never a silent pick;
     a decoy must not be able to absorb the comparison)
  3. for a time-varying property, what is the SETTLED value? (a defect that
     settles after a single-instant probe must not pass)

The JS collector and this mirror MUST keep identical thresholds — they are
the constants below. Pure functions, no I/O, unit-tested in isolation.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

# ── shared thresholds (mirror lib/visible-identity.js) ─────────────────
MIN_AREA_PX2: float = 4.0
"""Minimum rendered area (width*height). A sub-2x2 element paints nothing
meaningful; collapsed width:0 labels fall here too."""

MIN_FONT_PX: float = 4.0
"""Minimum computed font-size for text to count as painted. font-size:0
collapses scrollWidth/box to look measurable while showing nothing."""

DEFAULT_MARGIN_PX: float = 16.0
"""Off-screen tolerance. An element whose rect lies entirely beyond the
viewport plus this margin is not rendered to the user."""

ALPHA_FLOOR: float = 0.1
"""Minimum text colour alpha. rgba(...,0.01) renders nothing readable; the old
binary alpha>0 test let it pass."""

MIN_CONTRAST: float = 1.06
"""Minimum WCAG contrast ratio between the (alpha-blended) text colour and the
element's effective cascaded background. 1.0 == identical (white-on-white); any
real foreground/background difference clears 1.06."""

OPAQUE_ALPHA: float = 0.5
"""batch-9 ITEM 1: minimum effective alpha (opacity*background-color alpha) for
a foreign topmost node to count as OCCLUDING the text at a sampled point. A
fully transparent overlay or a translucent sticky-nav scrim (rgba(...,0.04))
falls below this and lets the text show through. The DECISION runs in the JS
collector; this constant keeps the two halves in lock-step."""

MATERIAL_OCCLUSION: float = 0.5
"""batch-9 ITEM 1: the fraction of MEASURED grid sample points that must be
opaquely occluded before the element reads "blocked". A 95%-covered text with a
clear centre clears this (multi-point), unlike the old single centre probe."""

BG_IMAGE_COVERAGE_FLOOR: float = 0.1
"""batch-9 ITEM 5: minimum fraction of the text rect that must be OPAQUELY
covered by the effective background image (sampled in the browser) before the
contrast check is skipped. A 1x1/mostly-transparent image with one opaque pixel
falls below this, so contrast still runs and invisible white-on-white over it is
caught. The DECISION (gate on the field) lives here; the sampling runs in the JS
collector — the two halves share this constant."""


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def opacity_of(rec: Mapping[str, Any]) -> float:
    return _f(rec.get("opacity"), 1.0)


def _rect(rec: Mapping[str, Any]) -> Mapping[str, Any]:
    r = rec.get("rect")
    return r if isinstance(r, Mapping) else {}


def _area(rec: Mapping[str, Any]) -> float:
    if rec.get("area") is not None:
        return _f(rec.get("area"))
    r = _rect(rec)
    return _f(r.get("width")) * _f(r.get("height"))


def is_laid_out(rec: Mapping[str, Any], *, min_area: float = MIN_AREA_PX2) -> bool:
    """Element occupies real, opaque layout space."""
    if str(rec.get("display", "")).lower() == "none":
        return False
    if str(rec.get("visibility", "")).lower() == "hidden":
        return False
    if opacity_of(rec) <= 0.0:
        return False
    r = _rect(rec)
    if _f(r.get("width")) <= 0.0 or _f(r.get("height")) <= 0.0:
        return False
    return _area(rec) >= min_area


def _viewport(
    rec: Mapping[str, Any], viewport: tuple[float, float] | None
) -> tuple[float, float]:
    if viewport is not None:
        return viewport
    return (_f(rec.get("clientWidth"), 0.0), _f(rec.get("clientHeight"), 0.0))


def is_on_screen(
    rec: Mapping[str, Any],
    *,
    viewport: tuple[float, float] | None = None,
    margin: float = DEFAULT_MARGIN_PX,
    below_fold_ok: bool = False,
) -> bool:
    """The element's rect intersects the viewport (plus margin).

    ``below_fold_ok`` keeps a BELOW-fold element (top beyond the viewport at the
    current scroll) on-screen: it is reachable by scrolling, and the masked
    driver scrolls each selector into view before measuring, so a faithful
    below-fold heading must not read "absent" (batch-8 ITEM 10). The x-axis and
    the ABOVE-viewport gate still reject an off-screen decoy (Attack C).
    """
    vp_w, vp_h = _viewport(rec, viewport)
    r = _rect(rec)
    left = _f(r.get("left"))
    top = _f(r.get("top"))
    right = left + _f(r.get("width"))
    bottom = top + _f(r.get("height"))
    if right <= -margin or left >= vp_w + margin:
        return False
    # Vertical intersection only gates when a viewport height is known; a
    # height of 0 means "unknown" (top is page-absolute in some artifacts),
    # so do not reject on the y-axis in that case.
    if vp_h > 0:
        if bottom <= -margin:
            return False
        if top >= vp_h + margin and not below_fold_ok:
            return False
    return True


def is_rendered(rec: Mapping[str, Any]) -> bool:
    """Browser-computed RENDER truth (close the imperceptibility class).

    Only rejects when a truth field is explicitly present AND hiding, so legacy
    records (and artifact-only callers) without these fields keep their geometry
    behaviour. Closes: Element.checkVisibility==false (cascaded ancestor
    opacity/visibility/display + content-visibility:auto off-screen),
    content-visibility:hidden with a laid-out box, fully-clipping clip-path/
    clip, filter:opacity(0), large off-box text-indent under an overflow clip,
    ancestor overflow-clip containment, and occlusion (the element is not the
    topmost painted node at its own centre).
    """
    if rec.get("checkVisibility") is False:
        return False
    if rec.get("clipFullyHidden") is True:
        return False
    if rec.get("filterOpacityZero") is True:
        return False
    if rec.get("ancestorClipped") is True:
        return False
    if rec.get("contentVisibilityHidden") is True:
        return False
    if rec.get("textIndentHidden") is True:
        return False
    ht = rec.get("hitTest")
    # null/absent == unknown (off-viewport / pointer-events:none) — do NOT treat
    # as hidden (false-positive guard). Only a concrete blocking result rejects.
    if isinstance(ht, str) and ht not in ("", "self", "descendant"):
        return False
    return True


def occluded_verdict(occluded: int, measured: int) -> str | None:
    """batch-9 ITEM 1: the pure tally verdict mirrored from the JS collector
    (lib/visible-identity.js occludedVerdict). ``None`` when no sample point
    could be measured (off-viewport / unmeasurable); ``"blocked"`` when at least
    MATERIAL_OCCLUSION of the measured points are opaquely occluded; ``"self"``
    otherwise. The multi-point sampling + paint-awareness that produce
    (occluded, measured) run in the browser; this keeps the threshold identical
    on the python side so the two halves cannot drift."""
    if measured <= 0:
        return None
    return "blocked" if (occluded / measured) >= MATERIAL_OCCLUSION else "self"


def _rgb(value: Any) -> tuple[float, float, float] | None:
    """Parse a colour to an (r,g,b) triple from a [r,g,b] list or an
    'rgb(r, g, b)' / 'rgba(...)' string. None when unparseable."""
    if isinstance(value, list | tuple) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().lower()
        op, cl = s.find("("), s.find(")")
        if op < 0 or cl < 0:
            return None
        parts = s[op + 1 : cl].split(",")
        if len(parts) < 3:
            return None
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        except (TypeError, ValueError):
            return None
    return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def chan(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2])


def contrast_ratio(
    fg: tuple[float, float, float],
    bg: tuple[float, float, float],
    alpha: float = 1.0,
) -> float:
    """WCAG contrast ratio between the alpha-blended foreground and the
    background. 1.0 when identical."""
    a = max(0.0, min(1.0, alpha))
    blended = tuple(fg[i] * a + bg[i] * (1.0 - a) for i in range(3))
    lum1 = _relative_luminance((blended[0], blended[1], blended[2]))
    lum2 = _relative_luminance(bg)
    hi, lo = max(lum1, lum2), min(lum1, lum2)
    return (hi + 0.05) / (lo + 0.05)


def paints_text(
    rec: Mapping[str, Any],
    *,
    min_font: float = MIN_FONT_PX,
    alpha_floor: float = ALPHA_FLOOR,
    min_contrast: float = MIN_CONTRAST,
) -> bool:
    """The element renders READABLE text: it has text, an alpha above the
    visibility floor, a font-size strictly above the minimum, and — when the
    collector emits resolved text colour + effective background — enough
    contrast against that background (white-on-white reads invisible)."""
    if not rec.get("hasText"):
        return False
    if _f(rec.get("colorAlpha"), 1.0) < alpha_floor:
        return False
    fs = rec.get("fontSizePx")
    if fs is not None and _f(fs) <= min_font:
        return False
    # Contrast vs the effective background — skipped ONLY with positive evidence
    # the bg-image PAINTS opaque pixels over the text box (a real hero photo:
    # effectiveBgImagePaints). The bare presence of a background-image is not
    # evidence — a 1x1/transparent/empty image or a same-colour gradient paints
    # nothing distinguishable, so contrast must still run there (invisible
    # white-on-white is caught). Alpha + font floors always apply, so
    # transparent/zero-font text over any image is still caught.
    # batch-9 ITEM 5: skip contrast ONLY when the bg-image paints AND its sampled
    # opaque coverage under the text rect clears the floor. A "paints" flag alone
    # (a mostly-transparent image with one opaque pixel, or an undecoded image
    # with no sampled coverage -> 0) no longer auto-passes invisible text.
    skip_contrast = bool(rec.get("effectiveBgImagePaints")) and _f(
        rec.get("bgImageOpaqueCoverage"), 0.0
    ) >= BG_IMAGE_COVERAGE_FLOOR
    if not skip_contrast:
        fg = _rgb(rec.get("color"))
        bg = _rgb(rec.get("effectiveBgColor"))
        if fg is not None and bg is not None:
            if contrast_ratio(fg, bg, _f(rec.get("colorAlpha"), 1.0)) < min_contrast:
                return False
    return True


def paints_content(
    rec: Mapping[str, Any],
    *,
    min_font: float = MIN_FONT_PX,
    alpha_floor: float = ALPHA_FLOOR,
    min_contrast: float = MIN_CONTRAST,
) -> bool:
    """The element paints SOMETHING a human can see: readable text, generated
    ::before/::after content, a non-transparent background colour, a background
    image, or replaced content (img/svg/canvas/video). A transparent zero-text
    spacer paints nothing and is not content."""
    if bool(rec.get("replaced")):
        return True
    if bool(rec.get("pseudoHasContent")):
        return True
    if _f(rec.get("bgColorAlpha")) > 0.0:
        return True
    if bool(rec.get("hasBgImage")):
        return True
    return paints_text(
        rec, min_font=min_font, alpha_floor=alpha_floor, min_contrast=min_contrast
    )


def is_visible(
    rec: Mapping[str, Any],
    *,
    viewport: tuple[float, float] | None = None,
    margin: float = DEFAULT_MARGIN_PX,
    min_area: float = MIN_AREA_PX2,
    min_font: float = MIN_FONT_PX,
    require_paint: bool = True,
    below_fold_ok: bool = False,
) -> bool:
    """Rendered (pixel-truth) AND laid out AND on-screen AND (optionally)
    painting content."""
    if not is_rendered(rec):
        return False
    if not is_laid_out(rec, min_area=min_area):
        return False
    if not is_on_screen(rec, viewport=viewport, margin=margin, below_fold_ok=below_fold_ok):
        return False
    if require_paint and not paints_content(rec, min_font=min_font):
        return False
    return True


class Resolution(NamedTuple):
    status: str  # "ok" | "ambiguous" | "none"
    visible: list[Mapping[str, Any]]
    target: Mapping[str, Any] | None
    reason: str


def resolve_visible(
    records: Sequence[Mapping[str, Any]],
    *,
    expected: int = 1,
    viewport: tuple[float, float] | None = None,
    margin: float = DEFAULT_MARGIN_PX,
    min_area: float = MIN_AREA_PX2,
    min_font: float = MIN_FONT_PX,
    require_paint: bool = True,
) -> Resolution:
    """Resolve a selector's matches to the rendered-visible target(s).

    FAILS LOUD (status="ambiguous") when more than `expected` matches are
    visible — a decoy must not be able to absorb the comparison. When the
    visible count equals `expected`, status="ok". When nothing visible
    remains, status="none" (the gate decides whether that is a fail/warn).
    """
    visible = [
        r
        for r in records
        if is_visible(
            r,
            viewport=viewport,
            margin=margin,
            min_area=min_area,
            min_font=min_font,
            require_paint=require_paint,
        )
    ]
    if len(visible) == expected:
        return Resolution("ok", visible, visible[0] if expected == 1 else None,
                          f"{len(visible)} visible match(es) == expected {expected}")
    if len(visible) > expected:
        return Resolution(
            "ambiguous", visible, None,
            f"{len(visible)} visible matches > expected {expected} "
            "(ambiguous decoy/duplicate)",
        )
    return Resolution(
        "none", visible, None,
        f"{len(visible)} visible matches < expected {expected} "
        "(rendered-visible target not found)",
    )


# ── settle: single-instant sampling defence ────────────────────────────


def settled_value(samples: Sequence[Any]) -> Any:
    """The value a property settles on after transients.

    A single-instant probe captures whatever state happens to render at that
    instant; a cheat can flip to the real defective state AFTER the probe
    window. We re-sample over a window and use the SETTLED (final stable)
    value — the state a real user is left looking at.
    """
    if not samples:
        raise ValueError("settled_value requires at least one sample")
    return samples[-1]


def is_settled(samples: Sequence[Any], *, window: int = 2) -> bool:
    """Whether the trailing `window` samples agree (quiescence reached)."""
    if len(samples) < window:
        return len(set_repr(samples)) <= 1
    tail = samples[-window:]
    first = tail[0]
    return all(s == first for s in tail)


SETTLE_FRAMES_DEFAULT: int = 3
SETTLE_FRAMES_MIN: int = 2


def settle_frames() -> int:
    """Number of trailing identical frames required to call a series settled.
    Env-tunable (UI_CLONE_SETTLE_FRAMES) but clamped to a sane floor — a
    1-frame "quiescence" is no quiescence at all."""
    try:
        v = int(os.environ.get("UI_CLONE_SETTLE_FRAMES", SETTLE_FRAMES_DEFAULT))
    except (TypeError, ValueError):
        v = SETTLE_FRAMES_DEFAULT
    return max(SETTLE_FRAMES_MIN, v)


def settled_state(
    samples: Sequence[Any], *, frames: int | None = None
) -> tuple[Any, bool]:
    """The SETTLED (last) state plus whether the series actually reached
    quiescence (the trailing `frames` samples agree).

    settled_value returns only the value (back-compat); the gates route through
    settled_state so a series that NEVER stabilises within the captured window
    is detectable — a late flip leaves the defect as the value AND flags the
    series as still-changing, so neither a transient-correct nor an oscillating
    series can mint a silent pass.
    """
    if not samples:
        raise ValueError("settled_state requires at least one sample")
    f = settle_frames() if frames is None else max(SETTLE_FRAMES_MIN, frames)
    value = samples[-1]
    if len(samples) < f:
        settled = len(set_repr(samples)) <= 1
    else:
        tail = samples[-f:]
        settled = all(s == tail[0] for s in tail)
    return value, settled


def set_repr(samples: Sequence[Any]) -> list[Any]:
    """Distinct values preserving order (works for unhashable values too)."""
    out: list[Any] = []
    for s in samples:
        if s not in out:
            out.append(s)
    return out
