"""Per-entry transition-fires decision logic.

The browser driving lives in
``skills/visual-debug/scripts/transition-fires-check.sh``; this module holds
the pure, testable decision: given a transition-spec entry and the before/after
runtime state measured around its driven trigger, decide whether the
transition actually FIRED.

FIX-NOT-LOOSEN: a PASS requires a MEASURED runtime delta on the target
(opacity / transform / rect / scroll-progress / currentTime / canvas-pixels)
in the expected direction. It can NOT be earned by a class name or a
``transition-`` token — that is exactly the hole the static name-match coverage
gate left open, where an unimplemented scroll-reveal "passed" because the class
string was in the JSX and a working FAQ "failed" because its spec id was not a
substring of the source.

A genuinely static page is NOT false-failed: only entries present in
``transition-spec.json`` are checked, so a page with no spec → no checks → not
failed (the caller short-circuits before reaching here).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ui_clone.gates.spec import _collect_dom_tokens, _token_present

SCHEMA_VERSION = 1

# Measurement thresholds — deliberately small so a real animation is caught,
# but above sub-pixel / float noise so a static element is not.
_OPACITY_EPS = 0.02
_TOP_EPS = 0.5      # px
_HEIGHT_EPS = 1.0   # px
_TIME_EPS = 0.05    # seconds (video currentTime advance)
_SCROLLABLE_DOC_PX = 300  # page must be at least this tall to expect scroll
_WHEEL_PAGE_MOVED_PX = 40  # rect.top span proving the wheel sweep drove the page

_IDENTITY_TRANSFORMS = {"", "none", "matrix(1,0,0,1,0,0)", "matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)"}

_EXPECTED = {
    "reveal": "opacity/transform advances when scrolled into view",
    "splash": "opacity/transform changes from initial to settled state on load",
    "sticky": "position: sticky pins at the declared top across the sticky range",
    "scrub": "transform/position varies across the scroll range",
    "smooth-scroll": "page position advances under scroll (engine wrapper translates)",
    "carousel": "slide transform/offset changes over time",
    "timer": "time-driven visual state changes while the target is visible",
    "hover": "computed style changes while hovered",
    "click": "target expands / opacity changes on click",
    "video": "currentTime advances after load",
    "webgl": "hero canvas renders non-blank pixels",
}


# ── captured-DOM target presence ─────────────────────────────────────────
# A transition-spec target whose class/id tokens are absent from the captured
# reference DOM (structure.json) was never on THIS page — it is a cross-page /
# subpage selector auto-mined from the site's full CSS (e.g. an IR download
# button's `:hover` rule captured on a homepage that has no such button). Such a
# target cannot fire on ref OR impl, so a downstream "element not found" is not a
# clone defect — it is a not-applicable probe. Reclassify it as KNOWN-SKIP, never
# a FAIL. Gated on the REF capture (not the impl), so a target that IS on the ref
# page but missing from the impl still FAILS as a real translation miss. Mirrors
# the exemptions of the draft-time gate ui_clone.gates.spec
# ._check_spec_selectors_present_in_dom (single behavioural contract; the stub
# entries that gate skips are exactly the ones that reach here).
_SEL_CLASS_RE = re.compile(r"\.((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")
_SEL_ID_RE = re.compile(r"#((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")
_SEL_NOISE_RE = re.compile(
    r"\[[^\]]*\]|(?<!\\)::?[A-Za-z][A-Za-z0-9-]*(?:\([^)]*\))?"
)
_SEL_RUNTIME_RE = re.compile(
    r"\b(?:swiper|splide|slick|flickity|embla|keen-slider|glide"
    r"|lottie|bodymovin|canvas"
    r"|lenis|locomotive|data-scroll|data-lottie|data-pseudo|data-lenis|data-smooth)"
)


def _sel_toks(rx: re.Pattern[str], group: str) -> list[str]:
    return [m.replace("\\", "") for m in rx.findall(group)]


def _target_absent_from_ref(
    target: str, classes: set[str], ids: set[str]
) -> bool:
    """True iff `target` is a class/id selector whose leaf tokens are ALL absent
    from the captured ref DOM (so it targets no element on this page).

    Conservative, matching the draft-time spec gate: runtime-injected selectors
    (swiper/lottie/canvas/lenis…) and tag-only / attribute-only targets are
    treated as PRESENT (never absent), so only a genuinely absent class/id
    selector is flagged. A comma list is present if ANY group is present.
    """
    if not target or not target.strip():
        return False
    if _SEL_RUNTIME_RE.search(target):
        return False
    cleaned = _SEL_NOISE_RE.sub(" ", target)
    if not _SEL_CLASS_RE.search(cleaned) and not _SEL_ID_RE.search(cleaned):
        return False  # tag-only / attribute-only — not reliably checkable
    for group in cleaned.split(","):
        group = group.strip()
        if not group:
            continue
        g_classes = _sel_toks(_SEL_CLASS_RE, group)
        g_ids = _sel_toks(_SEL_ID_RE, group)
        if not g_classes and not g_ids:
            return False  # a tag-only group makes the whole target present
        if all(_token_present(c, classes) for c in g_classes) and all(
            _token_present(d, ids) for d in g_ids
        ):
            return False  # this group is present → target is present
    return True  # no group matched → target absent from the captured page


# ── trigger classification ───────────────────────────────────────────────
def classify(entry: dict) -> str:
    """Map a spec entry to the runtime-measurement kind. Disambiguates the
    several page-load variants (video / webgl / splash / smooth-scroll) by the
    animation type, since they share trigger=page-load."""
    trig = str(entry.get("trigger", "")).lower()
    anim = entry.get("animation")
    if isinstance(anim, dict):
        atype = str(anim.get("type", "")).lower()
        aprop = str(anim.get("property", "")).lower()
        scrub_flag = bool(anim.get("scrub"))
    else:
        # Some extractors emit `animation` as a freeform description string
        # ("gsap.from y/opacity stagger ...") instead of a {type,...} dict.
        # Fold it into the classification blob so keyword matching still works
        # instead of crashing on `.get()` — a string-form spec otherwise takes
        # the whole gate down with 'str' has no attribute 'get'.
        atype = str(anim or "").lower()
        aprop = atype
        scrub_flag = "scrub" in atype
    blob = f"{trig} {atype}"
    # The kind signal often lives in the id/target, not trigger/animation.type
    # (bg-canvas, lenis, scroll-parallax) — running the gate on real sites
    # showed such entries falling through to the "reveal" fallback and being
    # false-negatived by reveal's single-scroll measurement. Boost the
    # strategy-critical kinds from id+target. Deliberately NOT video: an id/
    # target video signal would override a confident hover/click/splash whose
    # element merely contains a <video> (e.g. nivisgear t_hero_splash).
    eid = str(entry.get("id", "")).lower()
    etgt = str(entry.get("target", "")).lower()

    # CSS sticky is layout behavior, not a reveal. It commonly has a scroll
    # trigger but no opacity/transform delta at all; sending it through the
    # reveal fallback makes a faithful sticky header fail by construction.
    if "css-sticky" in atype or "css sticky" in atype:
        return "sticky"
    if "scroll" in trig and (
        "class-toggle" in atype or "classname" in aprop
    ):
        return "scrub"
    if "video" in blob:
        return "video"
    if ("webgl" in blob or "canvas" in blob
            or "webgl" in eid or "canvas" in eid or "canvas" in etgt):
        return "webgl"
    if "scrub" in blob or "progress" in blob or scrub_flag or "parallax" in blob or "parallax" in eid:
        return "scrub"
    # D17b (loop-nvti-0): a SCROLL-driven state machine (scroll position selects
    # the active paragraph/panel state) is scroll-position-driven exactly like a
    # scrub — a single scrollIntoView snap lands on ONE state and reads flat.
    # Only scroll triggers: click/hover state machines keep their own probes.
    if ("state machine" in blob or "state-machine" in blob) and "scroll" in trig:
        return "scrub"
    if ("smooth-scroll" in blob or "smoothscroll" in blob or "lenis" in blob
            or "smooth-scroll" in eid or "smoothscroll" in eid
            or "lenis" in eid or "lenis" in etgt):
        return "smooth-scroll"
    carousel_blob = f"{blob} {eid} {etgt}"
    if (
        "carousel" in carousel_blob
        or "slider" in carousel_blob
        or "slideshow" in carousel_blob
        or "swiper" in carousel_blob
        or "autoplay" in trig
    ):
        return "carousel"
    if any(k in carousel_blob for k in ("timer", "interval", "setinterval")):
        return "timer"
    if "click" in trig or "disclosure" in blob or "accordion" in blob:
        return "click"
    if "hover" in blob:
        return "hover"
    if "splash" in blob or "scale-in" in blob:
        return "splash"
    return "reveal"


# ── state helpers ────────────────────────────────────────────────────────
def _norm_transform(t: object) -> str:
    if t is None:
        return "I"
    s = str(t).replace(" ", "").lower()
    return "I" if s in _IDENTITY_TRANSFORMS else s


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _css_px(v: object) -> float | None:
    """Parse a finite CSS pixel value without treating ``auto`` as zero."""
    if isinstance(v, str):
        match = re.fullmatch(r"\s*(-?(?:\d+(?:\.\d*)?|\.\d+))px\s*", v)
        return float(match.group(1)) if match else None
    return _f(v)


def _declared_sticky_top(entry: dict) -> float | None:
    anim = entry.get("animation")
    if not isinstance(anim, dict):
        return None
    direct = _css_px(anim.get("top"))
    if direct is not None:
        return direct
    # Extractors commonly preserve CSS declarations in animation.from/to
    # rather than normalising top into its own field.
    for key in ("to", "from"):
        value = str(anim.get(key, ""))
        match = re.search(
            r"(?:^|[;,{]\s*)top\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))px\b",
            value,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1))
    return None


def _sticky_range(entry: dict) -> float | None:
    anim = entry.get("animation")
    values = []
    if isinstance(anim, dict):
        values.append(anim.get("stickyRangeH"))
    values.append(entry.get("stickyRangeH"))
    for value in values:
        parsed = _f(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _sticky_fired(entry: dict, before: dict, after: dict, samples: list[dict]) -> tuple[bool, str]:
    states = [before, after, *samples]
    positions = {
        str(state.get("position", "")).strip().lower()
        for state in states
        if state.get("position") is not None
    }
    if "sticky" not in positions:
        return False, f"computed position is not sticky ({sorted(positions) or ['missing']})"

    declared_top = _declared_sticky_top(entry)
    measured_tops = [
        value
        for state in states
        for value in (
            _css_px(
                state.get("cssTop", state.get("insetTop", state.get("stickyTop")))
            ),
        )
        if value is not None
    ]
    if not measured_tops:
        return False, "computed sticky top is missing or auto"
    expected_top = declared_top if declared_top is not None else measured_tops[-1]
    if declared_top is not None and not any(
        abs(value - declared_top) <= _TOP_EPS for value in measured_tops
    ):
        return (
            False,
            f"computed top {measured_tops[-1]}px does not match declared {declared_top}px",
        )

    pinned = []
    for sample in samples:
        viewport_top = _f(sample.get("top"))
        scroll_y = _f(sample.get("scrollY"))
        position = str(sample.get("position", "")).strip().lower()
        if (
            position in {"sticky", "-webkit-sticky"}
            and
            viewport_top is not None
            and scroll_y is not None
            and abs(viewport_top - expected_top) <= _TOP_EPS
        ):
            pinned.append((scroll_y, viewport_top))
    if len(pinned) < 2:
        return False, f"no sampled pinning at top={expected_top}px"

    pinned_span = max(scroll_y for scroll_y, _ in pinned) - min(
        scroll_y for scroll_y, _ in pinned
    )
    required_span = _sticky_range(entry)
    if required_span is None:
        required_span = _WHEEL_PAGE_MOVED_PX
    if pinned_span + _TOP_EPS < required_span:
        return (
            False,
            f"pinned scroll span {pinned_span}px is below expected {required_span}px",
        )
    return (
        True,
        f"position=sticky top={expected_top}px pinned across {pinned_span}px "
        f"(expected {required_span}px)",
    )


def _opacity_rose(before: dict, after: dict) -> bool:
    b, a = _f(before.get("opacity")), _f(after.get("opacity"))
    return b is not None and a is not None and (a - b) > _OPACITY_EPS


def _opacity_changed(before: dict, after: dict) -> bool:
    b, a = _f(before.get("opacity")), _f(after.get("opacity"))
    return b is not None and a is not None and abs(a - b) > _OPACITY_EPS


def _transform_changed(before: dict, after: dict) -> bool:
    return _norm_transform(before.get("transform")) != _norm_transform(after.get("transform"))


def _top_moved(before: dict, after: dict) -> bool:
    b, a = _f(before.get("top")), _f(after.get("top"))
    return b is not None and a is not None and abs(a - b) > _TOP_EPS


def _height_grew(before: dict, after: dict) -> bool:
    b, a = _f(before.get("height")), _f(after.get("height"))
    return b is not None and a is not None and (a - b) > _HEIGHT_EPS


def _height_changed(before: dict, after: dict) -> bool:
    b, a = _f(before.get("height")), _f(after.get("height"))
    return b is not None and a is not None and abs(a - b) > _HEIGHT_EPS


_COLOR_FIELDS = ("color", "backgroundColor", "borderColor")


def _color_changed(before: dict, after: dict) -> bool:
    # Real CSS :hover frequently only repaints color / background / border,
    # leaving opacity, transform and geometry untouched. Compare the computed
    # color fields the hover snapshot captures; any differing non-empty pair is
    # a measured visual change. Without this the gate false-negatives a working
    # CSS hover into "dead" just because it watched only opacity/transform.
    for k in _COLOR_FIELDS:
        b, a = before.get(k), after.get(k)
        if isinstance(b, str) and isinstance(a, str) and b and a and b != a:
            return True
    return False


def _child_changed(before: dict, after: dict) -> bool:
    # splittext / stagger animations move CHILD nodes (letters, words, lines)
    # while the container's own box stays flat. childSig is a compact join of
    # the first N descendants' transform|opacity; any differing non-empty
    # signature is a measured child-level delta the container-only checks miss
    # (split-text/reveal pattern: container static, child spans animate).
    b, a = before.get("childSig"), after.get("childSig")
    return isinstance(b, str) and isinstance(a, str) and bool(b) and b != a


def _child_opacities(sig: object) -> list[float]:
    ops = []
    for part in str(sig or "").split(";"):
        if "|" in part:
            v = _f(part.rsplit("|", 1)[1])
            if v is not None:
                ops.append(v)
    return ops


def _child_revealed(before: dict, after: dict) -> bool:
    # Reveal-specific child signal: a splittext / stagger reveal fades child
    # nodes IN (opacity 0 -> 1) while the container box stays flat. Count how
    # many children are hidden (opacity < 0.5) before vs after; a drop means
    # children appeared. Deliberately OPACITY-only, NOT transform: a child whose
    # transform changes (scroll-parallax child, a cursor that follows the
    # pointer) is not a reveal, and counting it reopened the Fix 43 loosening
    # hole (adcker highlight-reveal / custom-cursor spuriously passed).
    bo, ao = _child_opacities(before.get("childSig")), _child_opacities(after.get("childSig"))
    if not bo or not ao:
        return False
    b_hidden = sum(1 for o in bo if o < 0.5)
    a_hidden = sum(1 for o in ao if o < 0.5)
    return a_hidden < b_hidden


def _stroke_offset_px(v: object) -> float | None:
    s = str(v or "").strip()
    if s.endswith("px"):
        s = s[:-2]
    try:
        return float(s)
    except ValueError:
        return None


def _stroke_drew(before: dict, after: dict) -> bool:
    # Fix 78 — an SVG stroke draw-in animates strokeDashoffset (length -> 0),
    # touching NEITHER opacity nor transform, so the reveal judgment measured
    # "opacity 0 -> 0, transform I -> I" and false-negatived a firing draw.
    # Direction-aware on purpose (anti-loosening, mirrors _child_revealed):
    # only an offset whose magnitude DECREASED counts — the draw-in the spec
    # declares. Static dashes (no change) and draw-outs do not pass.
    b = _stroke_offset_px(before.get("strokeDashoffset"))
    a = _stroke_offset_px(after.get("strokeDashoffset"))
    if b is None or a is None:
        return False
    return abs(a) < abs(b) - _OPACITY_EPS


def _any_visual_change(before: dict, after: dict) -> bool:
    discrete_hover_change = any(
        before.get(key) is not None
        and after.get(key) is not None
        and str(before.get(key)) != str(after.get(key))
        for key in ("fontWeight", "pseudoBefore", "pseudoAfter")
    )
    return (
        _opacity_changed(before, after)
        or _transform_changed(before, after)
        or _top_moved(before, after)
        or _height_changed(before, after)
        or _color_changed(before, after)
        or discrete_hover_change
    )


def _reveal_probe_fired(probe: object) -> bool:
    """L-MEA-8 (loop-ebpb-0): a ONE-SHOT IO reveal that completes during the
    page-settle mount sweep reads before==after==final in the main pass, so
    its opacity/transform delta is gone by the time the post-sweep probe fires.
    The driver re-probes such entries from a FRESH navigate — target scrolled
    OUT of view for the true pre-state, then scrolled IN while sampled through
    the transition window. This is NOT the load-phase channel (which still
    excludes IO reveals, codex anti-bypass): the evidence is the pre-state vs
    the settled state (opacity rise / transform change / child fade-in) or
    variation across the in-flight samples. A re-probe that shows no motion
    keeps the honest fail.
    """
    if not isinstance(probe, dict):
        return False
    pre = probe.get("pre")
    samples = probe.get("samples") or []
    if isinstance(pre, dict):
        # C2: a resolvable pre-state (target snapshotted OUT of view) is the
        # AUTHORITATIVE baseline — compare it against the settled final sample
        # ONLY. A flat pre->final is a genuinely dead reveal; do NOT fall
        # through to in-flight sample variation, or time-driven churn in the
        # 0/300/900ms window (an autoplaying carousel / pulsing child inside
        # the reveal target) would false-pass a dead reveal.
        if not samples:
            return False
        final = samples[-1]
        return (
            _opacity_rose(pre, final)
            or _transform_changed(pre, final)
            or _child_revealed(pre, final)
        )
    # No resolvable pre-state (a conditional-mount impl never rendered the
    # target at top): the only evidence is in-flight variation as it scrolls in.
    return _samples_vary(samples)


def _not_active_intersection_target(target: str) -> bool:
    return bool(re.search(r":not\(\s*\.active\s*\)", target, re.IGNORECASE))


def _has_positive_duration(value: object) -> bool:
    for token in re.split(r"[,;]", str(value or "")):
        match = re.fullmatch(r"\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(ms|s)\s*", token)
        if not match:
            continue
        duration_ms = float(match.group(1)) * (1000 if match.group(2) == "s" else 1)
        if duration_ms > 0:
            return True
    return False


def _sample_declares_temporal_motion(sample: object) -> bool:
    if not isinstance(sample, dict):
        return False
    if _has_positive_duration(sample.get("transitionDuration")):
        return True
    if _has_positive_duration(sample.get("childTransitionDuration")):
        return True
    animation_name = str(sample.get("animationName") or "").strip().lower()
    if animation_name not in {"", "none"} and _has_positive_duration(
        sample.get("animationDuration")
    ):
        return True
    return _has_positive_duration(sample.get("childAnimationDuration"))


def _reveal_probe_temporal_fired(probe: object) -> bool:
    """Strict evidence for ``:not(.active)`` intersection reveals.

    For selectors that stop matching once the subject becomes active, before /
    after endpoint snapshots can land on different below-fold elements. Require
    visual motion across the fresh in-flight samples themselves; class churn
    alone is not motion, and a zero-duration jump that is already settled at the
    first sample stays flat.
    """
    if not isinstance(probe, dict):
        return False
    samples = probe.get("samples") or []
    if not isinstance(samples, list) or len(samples) < 2:
        return False
    if not any(_sample_declares_temporal_motion(sample) for sample in samples):
        return False
    first = samples[0]
    if not isinstance(first, dict):
        return False
    for sample in samples[1:]:
        if not isinstance(sample, dict):
            continue
        if (
            _opacity_rose(first, sample)
            or _transform_changed(first, sample)
            or _child_revealed(first, sample)
        ):
            return True
    return False


def _load_phase_fired(load_samples: list, kind: str) -> bool:
    """Load-phase series evidence for motion that completes BEFORE the
    post-sweep probe (loop-e2e-5): splash overlays settle ~2.45s after
    navigate, IO reveals fire during the mount sweep, stroke draws complete
    at mount, and timer carousels rotate CONTENT (img srcs) rather than
    transforms. The series is sampled on a fresh navigate, so variation here
    is the firing itself — not scroll-coupled noise. Carousel kind requires
    an img-src set change; others accept opacity/transform/childSig variation
    or a stroke offset whose magnitude decreases (draw-in only,
    anti-loosening like _stroke_drew)."""
    if not load_samples:
        return False
    if kind == "carousel":
        sets = [tuple(s.get("imgSrcs") or ()) for s in load_samples]
        return any(a != b for a, b in zip(sets, sets[1:], strict=False) if a or b)
    first = load_samples[0]
    f_t = _norm_transform(first.get("transform"))
    f_op = _f(first.get("opacity"))
    f_sig = str(first.get("childSig") or "")
    f_st = _stroke_offset_px(first.get("strokeDashoffset"))
    for s in load_samples[1:]:
        if _norm_transform(s.get("transform")) != f_t:
            return True
        op = _f(s.get("opacity"))
        if f_op is not None and op is not None and abs(op - f_op) > _OPACITY_EPS:
            return True
        sig = str(s.get("childSig") or "")
        if f_sig and sig and sig != f_sig:
            return True
        st = _stroke_offset_px(s.get("strokeDashoffset"))
        if f_st is not None and st is not None and abs(st) < abs(f_st) - _OPACITY_EPS:
            return True
    return False


def _timer_samples_vary(samples: list) -> bool:
    """Return measured visual variation for a visible timer-driven target.

    Timer UIs commonly replace their target node on every tick (React `key`
    remounts are a frequent example), so the browser probe re-resolves the
    selector and records both the target and descendant visual signatures.
    Geometry is intentionally excluded: scrolling the target into view moves
    its viewport box even when the timer is dead.
    """
    if len(samples) < 2:
        return False
    first = samples[0]
    first_child = str(first.get("childVisualSig") or "")
    for sample in samples[1:]:
        if (
            _opacity_changed(first, sample)
            or _transform_changed(first, sample)
            or _color_changed(first, sample)
        ):
            return True
        child = str(sample.get("childVisualSig") or "")
        if first_child and child and child != first_child:
            return True
    return False


def _anim_prop(entry: dict) -> str:
    """Spec-declared animation property (lowercased). Gates measurement
    channels the same way e.prop already gates strokeDashoffset lookup —
    a channel only counts when the spec declares that property family."""
    anim = entry.get("animation")
    if isinstance(anim, dict):
        return str(anim.get("property", "")).lower()
    return str(anim or "").lower()


def _hover_descendant_measurement_declared(entry: dict) -> bool:
    anim = entry.get("animation")
    return (
        isinstance(anim, dict)
        and anim.get("measurement") == "target-and-descendants"
    )


def _child_height_grew(before: dict, after: dict) -> bool:
    """Fix M (loop-e2e-6): bar-grow reveals animate descendant heights 0->Npx
    with no opacity/transform delta. Growth must start from ~0 (a bar at
    rest) and clear a jitter threshold — a partially-rendered box (lazy
    media / font reflow) starts above 1px and is excluded driver-side by
    skipping replaced elements (img/video/iframe/svg/canvas)."""
    b = before.get("childHeights")
    a = after.get("childHeights")
    if not isinstance(b, list) or not isinstance(a, list):
        return False
    for bb_raw, aa_raw in zip(b, a, strict=False):
        bb, aa = _f(bb_raw), _f(aa_raw)
        if bb is None or aa is None:
            continue
        if bb <= 1.0 and aa > 8.0:
            return True
    return False


def _text_digest_changed(before: dict, after: dict) -> bool:
    """Fix M (loop-e2e-6): numeric count-ups mutate textContent only. The
    digest (digits of innerText) is compared PRE-sweep vs post-trigger; the
    channel is property-gated (count/textcontent) by the caller. Residual
    live-clock risk is bounded by spec provenance — a spec entry declaring a
    count-up over a clock region would fail duration-easing-grounding."""
    b, a = before.get("textDigest"), after.get("textDigest")
    if b is None or a is None:
        return False
    return str(b) != str(a)


def _samples_vary(samples: list, prop: str = "", decl_blob: str = "") -> bool:
    # Scrub fires by animating transform/opacity across the scroll range.
    # Deliberately NOT viewport `top`: getBoundingClientRect().top moves on
    # ANY page scroll, so counting it would PASS an unimplemented scrub on
    # natural scroll alone (a loosening hole).
    if not samples:
        return False
    first = samples[0]
    f_t = _norm_transform(first.get("transform"))
    f_op = _f(first.get("opacity"))
    # Child-signature + inline-width series (loop-e2e-5): scrub motion often
    # lives in DESCENDANTS (deck cards, word-reveal spans) or in an inline
    # width track (hero video 80vw->100vw) — the target's own transform stays
    # I. childSig carries only children's transform|opacity (no
    # scroll-coupled positions) and width does not move under natural scroll,
    # so neither loosens the `top` exclusion above.
    f_sig = str(first.get("childSig") or "")
    f_w = _f(first.get("width"))
    # Fix L (loop-e2e-6): word-reveal scrubs swap classes that change computed
    # COLOR only (dimmed -> highlight; opacity stays 1). The color series is a
    # separate field and counts ONLY when the spec declares a color-family
    # property (codex review: animation-timeline:scroll() / body.scrolled-class
    # color shifts could otherwise false-pass a dead scrub).
    declares_color = any(k in prop for k in ("color", "fill", "stroke"))
    f_csig = str(first.get("childColorSig") or "")
    # L-MEA-2 (loop-ebpb-0): a scroll-driven CLASS TOGGLE (a fixed header that
    # gains a shadow class once scrollY>0, animating box-shadow) mutates the
    # target's className string while transform/opacity/childSig/width all stay
    # flat — invisible to every channel above. The className is a separate
    # per-sample field and counts ONLY when the spec-side decl blob
    # (id/trigger/property) declares a class/state/toggle change (same
    # decl_blob discipline as D17c count-up), and ONLY across advanced scroll —
    # so undeclared className churn (a carousel auto-advancing its active-slide
    # class) cannot false-pass a dead scrub.
    declares_class_toggle = any(
        k in decl_blob for k in ("class", "toggle", "state")
    )
    f_cls_tokens = set(str(first.get("cls") or "").split())
    # verify-H2: a running CSS animation on the TARGET (marquee/float/pulse)
    # varies its own transform/opacity on a TIMER, not in response to scroll —
    # the exact noise scroll-end-completion skips at collection. When any sample
    # flags a running animation, the target's own transform/opacity is
    # unreliable scrub evidence, so skip those two channels and fall through to
    # the advance-gated child/width/color/class channels rather than false-pass
    # a dead scrub. (When no animation runs, behavior is unchanged.)
    anim_running = any(bool(s.get("animRunning")) for s in samples)
    for s in samples[1:]:
        if not anim_running:
            if _norm_transform(s.get("transform")) != f_t:
                return True
            op = _f(s.get("opacity"))
            if f_op is not None and op is not None and abs(op - f_op) > _OPACITY_EPS:
                return True
        sig = str(s.get("childSig") or "")
        w = _f(s.get("width"))
        csig = str(s.get("childColorSig") or "")
        # Anti-bypass (codex review): childSig/width variation counts only
        # when the page actually ADVANCED between the samples — a load-time
        # child opacity flip at frozen scroll is not scrub evidence.
        y0 = _f(samples[0].get("scrollY"))
        y1 = _f(s.get("scrollY"))
        advanced = y0 is not None and y1 is not None and abs(y1 - y0) > 1.0
        if advanced and f_sig and sig and sig != f_sig:
            return True
        if advanced and f_w is not None and w is not None and abs(w - f_w) > 1.0:
            return True
        if advanced and declares_color and f_csig and csig and csig != f_csig:
            return True
        cls_tokens = set(str(s.get("cls") or "").split())
        # C3: the class/state/toggle keyword gate is too weak on its own —
        # "state" appears in ordinary ids (hero-paragraph-state-machine), so a
        # timer-driven class churn (a carousel active-slide class) that happens
        # to span the wall-time of the scroll sweep would false-pass a dead
        # scrub. Require the CHANGED class TOKEN itself to be NAMED in the
        # spec-side decl blob — specs name their state classes (is-scroll,
        # hide, style_header__shadow). A module-hash suffix is stripped so a
        # regenerated impl hash still matches its stable base token.
        if advanced and declares_class_toggle and cls_tokens != f_cls_tokens:
            for tok in cls_tokens ^ f_cls_tokens:
                base = re.sub(r"__[A-Za-z0-9_-]{4,}$", "", tok)
                if any(len(c) >= 4 and c in decl_blob for c in (tok, base)):
                    return True
    return False


def _declares_per_element(entry: dict) -> bool:
    """True for scrub entries whose spec declares PER-ELEMENT evolution
    (per-card y/x/rotate/z-index at progress fractions). For these, binary
    "something varied" is not sufficient evidence — observed failure mode:
    a sticky deck whose container transform varied while every card held a
    static fan passed the old check with the reshuffle completely dead."""
    prop = _anim_prop(entry)
    has_transform = any(
        k in prop for k in ("transform", "translate", " x", " y", "x,", "y,", "rotate")
    )
    has_per_element_channel = (
        "rotate" in prop or "z-index" in prop or "zindex" in prop
    )
    return has_transform and has_per_element_channel


def _parse_child_states(sig: str) -> list[tuple[float, float, float, float | None]]:
    """Per-child (tx, ty, rotationComponent, zIndex|None) from a childSig.

    Driver format: `transform|opacity[|zIndex];` per child — the zIndex field
    is absent in pre-extension payloads (legacy two-field sigs still drive
    the transform channels)."""
    out: list[tuple[float, float, float, float | None]] = []
    for chunk in str(sig or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = chunk.split("|")
        transform = fields[0].strip()
        z: float | None = None
        if len(fields) >= 3:
            z = _f(fields[2])
        tx = ty = rot = 0.0
        m = re.search(r"matrix\(([^)]+)\)", transform)
        m3 = re.search(r"matrix3d\(([^)]+)\)", transform)
        if m3:
            vals = [_f(v) for v in m3.group(1).split(",")]
            if len(vals) >= 14:
                rot = vals[1] or 0.0
                tx = vals[12] or 0.0
                ty = vals[13] or 0.0
        elif m:
            vals = [_f(v) for v in m.group(1).split(",")]
            if len(vals) >= 6:
                rot = vals[1] or 0.0
                tx = vals[4] or 0.0
                ty = vals[5] or 0.0
        out.append((tx, ty, rot, z))
    return out


_PE_POS_EPS = 1.0
_PE_ROT_EPS = 0.01


def _per_element_motion(samples: list) -> dict:
    """Per-child motion evidence across advanced-scroll samples."""
    y0 = _f(samples[0].get("scrollY")) if samples else None
    rows: list[list[tuple[float, float, float, float | None]]] = []
    for s in samples:
        y = _f(s.get("scrollY"))
        if rows and y0 is not None and y is not None and abs(y - y0) <= 1.0:
            continue  # keep first sample; later frozen-scroll dupes add nothing
        rows.append(_parse_child_states(str(s.get("childSig") or "")))
    n = min((len(r) for r in rows), default=0)
    if len(rows) < 2 or n < 2:
        return {"measurable": False, "changedChildren": 0, "relativeChanged": False}

    def _diff(a: tuple, b: tuple) -> bool:
        if abs(a[0] - b[0]) > _PE_POS_EPS or abs(a[1] - b[1]) > _PE_POS_EPS:
            return True
        if abs(a[2] - b[2]) > _PE_ROT_EPS:
            return True
        if a[3] is not None and b[3] is not None and abs(a[3] - b[3]) > 0.5:
            return True
        return False

    changed = 0
    translate_changed = False
    rotate_changed = False
    z_changed = False
    z_measurable = False
    for child in range(n):
        series = [r[child] for r in rows]
        if any(_diff(series[0], state) for state in series[1:]):
            changed += 1
        first = series[0]
        for state in series[1:]:
            if (
                abs(state[0] - first[0]) > _PE_POS_EPS
                or abs(state[1] - first[1]) > _PE_POS_EPS
            ):
                translate_changed = True
            if abs(state[2] - first[2]) > _PE_ROT_EPS:
                rotate_changed = True
            if state[3] is not None and first[3] is not None:
                z_measurable = True
                if abs(state[3] - first[3]) > 0.5:
                    z_changed = True

    relative_changed = False
    for i in range(n):
        for j in range(i + 1, n):
            base = None
            for r in rows:
                a, b = r[i], r[j]
                delta = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
                if base is None:
                    base = delta
                    continue
                if (
                    abs(delta[0] - base[0]) > _PE_POS_EPS
                    or abs(delta[1] - base[1]) > _PE_POS_EPS
                    or abs(delta[2] - base[2]) > _PE_ROT_EPS
                ):
                    relative_changed = True
                    break
            if relative_changed:
                break
        if relative_changed:
            break

    return {
        "measurable": True,
        "children": n,
        "changedChildren": changed,
        "relativeChanged": relative_changed,
        "translateChanged": translate_changed,
        "rotateChanged": rotate_changed,
        "zChanged": z_changed,
        "zMeasurable": z_measurable,
    }


def _missing_declared_channels(entry: dict, pem: dict) -> list[str]:
    """Spec-declared per-element channels with no observed motion.

    (a) of the scrub-mechanics contract: values must CHANGE across the
    sampled fractions on each DECLARED channel — rotation alone cannot
    satisfy a spec that declares y/x and z-index evolution (observed
    failure mode: deck cards picked up small rotations while translation
    and z-order never moved)."""
    prop = _anim_prop(entry)
    missing: list[str] = []
    declares_translate = bool(
        "translate" in prop or re.search(r"\b[xy]\b", prop)
    )
    declares_rotate = "rotate" in prop
    declares_z = "z-index" in prop or "zindex" in prop
    if declares_translate and not pem.get("translateChanged"):
        missing.append("translate(x/y)")
    if declares_rotate and not pem.get("rotateChanged"):
        missing.append("rotate")
    if declares_z:
        if not pem.get("zMeasurable"):
            missing.append("z-index (no z data in child signatures)")
        elif not pem.get("zChanged"):
            missing.append("z-index")
    return missing


def _scroll_blocked(samples: list) -> bool:
    # Smooth-scroll engines (Lenis / ScrollSmoother) intercept window.scrollTo,
    # so the page never advances under programmatic scroll and the scrub cannot
    # be driven at all. Detect it: a scrollable page (docH large) where scrollY
    # never changed across the samples. A flat transform under THAT is "couldn't
    # measure", not "dead" — distinct from a genuine flat scrub where the scroll
    # did advance. Requires the driver to record scrollY + docH per sample;
    # absent (older payloads / short pages) -> not blocked, keep normal verdict.
    ys = [y for y in (_f(s.get("scrollY")) for s in samples) if y is not None]
    docs = [d for d in (_f(s.get("docH")) for s in samples) if d is not None]
    if len(ys) < 2 or not docs:
        return False
    scrollable = max(docs) > _SCROLLABLE_DOC_PX
    advanced = len({round(y) for y in ys}) > 1
    engine = any(bool(s.get("smoothEngine")) for s in samples)
    # D17a (loop-nvti-0): when the driver AFFIRMATIVELY recorded that no smooth
    # engine is present (smoothEngine key exists and is falsy on every sample),
    # a frozen scrollY is the sampler's own sweep clamp (near-top elements),
    # NOT an engine intercept — reporting "smooth-scroll intercept" there
    # mislabeled a measurable page as unmeasurable. Legacy payloads without
    # the key keep the frozen-scroll inference below.
    engine_key_present = any("smoothEngine" in s for s in samples if isinstance(s, dict))
    if engine_key_present and not engine:
        return False
    # Two intercept modes: wrapper-mode (scrollY frozen, never advances) and
    # native-mode (scrollY advances but the scrub is bound to the engine's
    # virtual position a jump-scroll can't drive). Either way, on a scrollable
    # page, a flat transform is "couldn't drive", not "dead".
    return scrollable and (not advanced or engine)


# ── per-entry decision ───────────────────────────────────────────────────
_SKIP_KEYS = ("originLockedSkips", "substitutions", "skips")
_SKIP_ID_FIELDS = ("id", "target", "selector", "transitionId")


def partition_skip_ids(asset_sub: dict) -> tuple[set[str], set[str]]:
    """Split declared transition exemptions into (honored, unreasoned).

    An exemption must state WHY. `asset-substitution.md` already requires a
    `reason` on every fonts/images/videos substitution, and image
    substitutions are corroborated against a matching `download-log.json`
    failure. The transition channel had no such rule: any id listed here was
    honored with no reason and no cap, so this block-severity motion gate
    could be passed by listing every transition that did not fire.

    Reasonless dict entries and bare strings (which cannot carry a reason by
    construction) are NOT honored — they fall through to normal measurement.
    They are returned separately so the artifact can report them rather than
    dropping them silently.
    """
    honored: set[str] = set()
    unreasoned: set[str] = set()
    if not isinstance(asset_sub, dict):
        return honored, unreasoned
    for key in _SKIP_KEYS:
        for item in asset_sub.get(key) or []:
            if isinstance(item, str):
                unreasoned.add(item)
                continue
            if not isinstance(item, dict):
                continue
            ids = {str(item[k]) for k in _SKIP_ID_FIELDS if item.get(k)}
            if not ids:
                continue
            reasoned = bool(str(item.get("reason") or "").strip())
            (honored if reasoned else unreasoned).update(ids)
    return honored, unreasoned - honored


def load_skip_ids(asset_sub: dict) -> set[str]:
    """Ids/targets legitimately exempt (paid-lib substitution or origin-locked
    WebGL) per asset-substitution.json, each carrying a non-empty `reason`.
    These count as KNOWN-SKIP — still listed, never a silent pass."""
    return partition_skip_ids(asset_sub)[0]


def _anim_type(entry: dict) -> str:
    # `animation` may be a {type,...} dict or a freeform description string
    # (extractor variant). Never assume dict — both classify() and decide()
    # crashed on the string form (regression: adcker spec, exit 2).
    anim = entry.get("animation")
    if isinstance(anim, dict):
        return str(anim.get("type", ""))
    return str(anim or "")


def _is_reset_only_hover(entry: dict) -> bool:
    """Return true for hover specs that encode a reset, not motion.

    Some production CSS bundles include global reset rules such as
    ``a:hover { text-decoration:none }``. When the idle state is already
    ``text-decoration:none`` those rules have no runtime delta by design; a
    clone should not fake a hover color/transform only to satisfy the motion
    gate. Treat only this narrow no-op reset shape as a known skip.
    """

    anim = entry.get("animation")
    if isinstance(anim, dict):
        blob = f"{anim.get('type', '')} {anim.get('cssText', '')} {anim.get('css', '')}"
    else:
        blob = str(anim or "")

    text = blob.lower()
    if "hover" not in f"{entry.get('trigger', '')} {text}".lower():
        return False
    if not re.search(r"text-decoration(?:-line)?\s*:\s*none\b", text):
        return False

    # Do not skip real hover motion or visible styling.
    visible_or_motion_decl = re.compile(
        r"(?:transition|animation|transform|opacity|filter|box-shadow|"
        r"background(?:-color)?|color|fill|stroke|width|height|top|right|"
        r"bottom|left)\s*:"
    )
    return visible_or_motion_decl.search(text) is None


def decide(
    entry: dict, obs: dict, skip_ids: set[str], absent_from_ref: bool = False
) -> dict:
    eid = str(entry.get("id", ""))
    target = str(entry.get("target", ""))
    kind = classify(entry)
    res = {
        "id": eid,
        "trigger": str(entry.get("trigger", "")),
        "type": _anim_type(entry),
        "kind": kind,
        "expected": _EXPECTED.get(kind, "measured runtime motion"),
        "observed": "",
        "status": "fail",
    }

    if (eid and eid in skip_ids) or (target and target in skip_ids):
        res["status"] = "known-skip"
        res["observed"] = "exempt: documented asset-substitution / origin-lock skip"
        return res

    if not obs.get("found"):
        if absent_from_ref:
            # Target's class/id tokens are absent from the captured ref DOM: it
            # was never on this page (cross-page/subpage selector auto-mined from
            # the site's full CSS). Not a clone defect — a not-applicable probe.
            res["status"] = "known-skip"
            res["observed"] = (
                "known-skip: target absent from the captured reference page "
                "(cross-page/subpage selector) — not a transition this page has"
            )
            return res
        res["status"] = "fail"
        res["observed"] = "element not found for target selector"
        return res

    before = obs.get("before") or {}
    after = obs.get("after") or {}
    samples = obs.get("samples") or []

    if kind == "sticky":
        fired, observed = _sticky_fired(entry, before, after, samples)
        res["observed"] = observed
        res["status"] = "pass" if fired else "fail"
        return res

    if kind == "video":
        b, a = _f(before.get("currentTime")), _f(after.get("currentTime"))
        adv = (b is not None and a is not None and (a - b) > _TIME_EPS)
        res["observed"] = f"currentTime {b} -> {a}"
        res["status"] = "pass" if adv else "fail"
        return res

    if kind == "webgl":
        count = int(after.get("canvasCount") or 0)
        nonblank = bool(after.get("canvasNonBlank"))
        res["observed"] = f"canvasCount={count} nonBlank={nonblank}"
        res["status"] = "pass" if (count >= 1 and nonblank) else "fail"
        return res

    if kind == "scrub":
        if samples:
            # L-MEA-2 / C3: hand the className channel a RICH spec-side decl
            # blob so a declared class token can be matched by name. Real specs
            # name their state class in description/bundle_branch (not only
            # id/trigger/property) and the from/to animation fields. All are
            # spec-side (the impl cannot influence them), preserving the
            # anti-bypass contract.
            anim = entry.get("animation")
            anim_txt = ""
            if isinstance(anim, dict):
                anim_txt = " ".join(
                    str(anim.get(k, "")) for k in ("property", "from", "to")
                )
            scrub_blob = " ".join([
                _anim_prop(entry),
                str(entry.get("id", "")),
                str(entry.get("trigger", "")),
                str(entry.get("description", "")),
                str(entry.get("bundle_branch", "")),
                anim_txt,
            ]).lower()
            varied = _samples_vary(samples, _anim_prop(entry), scrub_blob)
            wheel = any(bool(s.get("wheelDriven")) for s in samples)
            if not varied and wheel:
                # Wheel re-probe drove the engine directly. If the element
                # demonstrably moved through the viewport while transform/
                # opacity stayed flat, the scrub is DEAD — the old
                # "unmeasurable" excuse no longer applies.
                # Page advancement has TWO independent witnesses: the element's
                # rect.top moving through the viewport, OR scrollY advancing.
                # The latter is essential for position:fixed scrub targets
                # (floating nav, scroll-progress dots) whose rect.top is constant
                # by definition — the engine-driven re-probe (window.__lenis
                # .scrollTo) advances the real virtual scroll, so a scrollY delta
                # proves the page moved even when the element's top cannot.
                tops = [v for v in (_f(s.get("top")) for s in samples) if v is not None]
                scrolls = [v for v in (_f(s.get("scrollY")) for s in samples) if v is not None]
                smooth = any(bool(s.get("smoothEngine")) for s in samples)
                engine_driven = any(bool(s.get("engineDriven")) for s in samples)
                el_moved = (
                    len(tops) >= 2 and (max(tops) - min(tops)) > _WHEEL_PAGE_MOVED_PX
                )
                scroll_advanced = (
                    len(scrolls) >= 2 and (max(scrolls) - min(scrolls)) > _WHEEL_PAGE_MOVED_PX
                )
                # A scrollY climb only proves the SCRUB engine advanced when the
                # page is not a smooth engine, or the re-probe actually drove that
                # engine's own API. A smooth engine present but NOT API-drivable
                # means scrollY rose via the native-scrollTo fallback WITHOUT
                # advancing the engine's virtual scroll, so a flat scrub there is
                # unmeasurable, not dead (mirrors _scroll_blocked's native mode).
                page_advanced = scroll_advanced and (engine_driven or not smooth)
                if el_moved or page_advanced:
                    res["status"] = "fail"
                    res["observed"] = (
                        "engine-driven re-probe advanced the page "
                        f"({'scrollY moved' if page_advanced else 'element moved through the viewport'}) "
                        "but transform/opacity stayed flat — scrub is dead, "
                        "not unmeasurable"
                    )
                elif smooth and scroll_advanced and not engine_driven:
                    res["status"] = "unmeasurable"
                    res["observed"] = (
                        "smooth-scroll engine present but no drivable API "
                        "(__lenis/ScrollSmoother absent); native scroll advanced "
                        "scrollY without driving the engine's virtual position — "
                        "scrub unmeasurable, not dead"
                    )
                else:
                    res["status"] = "unmeasurable"
                    res["observed"] = (
                        "engine-driven re-probe could not advance the scroll "
                        "engine (neither scrollY nor the element moved); scrub "
                        "unmeasurable"
                    )
                return res
            if not varied and _scroll_blocked(samples):
                res["status"] = "unmeasurable"
                res["observed"] = (
                    "smooth-scroll engine intercepts programmatic scroll "
                    "(page did not advance); scrub unmeasurable, not dead "
                    "(no wheel re-probe samples present)"
                )
                return res
            # Per-element scrub mechanics: when the spec declares per-card
            # y/x/rotate/z-index evolution, binary "varied" is not enough —
            # the per-child series must change AND the cards must move
            # relative to each other (a fan translating as one block is a
            # dead reshuffle).
            if varied and _declares_per_element(entry):
                pem = _per_element_motion(samples)
                if not pem["measurable"]:
                    res["status"] = "fail"
                    res["observed"] = (
                        "spec declares per-element scrub params but per-element "
                        "evidence is missing (no usable child signatures in the "
                        "samples) — binary variation alone is not sufficient"
                    )
                    return res
                if pem["changedChildren"] < 2:
                    res["status"] = "fail"
                    res["observed"] = (
                        f"per-element channels flat: {pem['changedChildren']}/"
                        f"{pem['children']} children changed across the sampled "
                        "scroll fractions while the spec declares per-card "
                        "y/x/rotate/z-index evolution — reshuffle is dead"
                    )
                    return res
                missing_channels = _missing_declared_channels(entry, pem)
                if missing_channels:
                    res["status"] = "fail"
                    res["observed"] = (
                        "declared channel(s) without motion across the sampled "
                        f"fractions: {missing_channels} — partial motion on "
                        "other channels does not satisfy a per-element scrub "
                        "spec"
                    )
                    return res
                if not pem["relativeChanged"]:
                    res["status"] = "fail"
                    res["observed"] = (
                        "cards moved as one block — no relative differentiation "
                        "between children across the sampled fractions (static "
                        "fan), while the spec declares per-card reshuffle"
                    )
                    return res
                res["observed"] = (
                    f"{len(samples)} scroll samples, varied=True, per-element: "
                    f"{pem['changedChildren']}/{pem['children']} children "
                    "changed with relative differentiation"
                )
                res["status"] = "pass"
                return res
            res["observed"] = f"{len(samples)} scroll samples, varied={varied}"
            res["status"] = "pass" if varied else "fail"
        else:
            changed = _any_visual_change(before, after)
            res["observed"] = "no samples; before/after change=" + str(changed)
            res["status"] = "pass" if changed else "fail"
        return res

    if kind == "smooth-scroll":
        moved = _top_moved(before, after) or _transform_changed(before, after)
        engine = _transform_changed(before, after)  # wrapper translate signature
        if not moved:
            res["observed"] = "page did not move under scroll"
            res["status"] = "fail"
        elif engine:
            res["observed"] = "page moved with transform-wrapper (smooth engine present)"
            res["status"] = "pass"
        else:
            res["observed"] = "page scrolls but no smooth-engine wrapper (native scroll)"
            res["status"] = "degraded"
        return res

    if kind == "carousel":
        moved = (
            _transform_changed(before, after)
            or _samples_vary(samples, _anim_prop(entry))
            or _top_moved(before, after)
        )
        # scrollLeft offset (some sliders translate via scroll, not transform)
        sl_b, sl_a = _f(before.get("scrollLeft")), _f(after.get("scrollLeft"))
        if sl_b is not None and sl_a is not None and abs(sl_a - sl_b) > _TOP_EPS:
            moved = True
        # Effect-agnostic carousel channel: the probe captures an active-index +
        # per-slide-opacity fingerprint before/after. A FADE carousel holds its
        # wrapper transform at identity, so the transform channel above never
        # varies even though the slideshow is running — the fingerprint change is
        # the signal it actually advanced. A dead carousel yields an identical
        # fingerprint, so this cannot false-pass (index and opacities are stable).
        car = obs.get("carousel")
        if isinstance(car, dict) and car.get("before") != car.get("after"):
            moved = True
        if not moved and _load_phase_fired(obs.get("loadSamples") or [], "carousel"):
            res["observed"] = "load-phase img-src rotation observed (timer carousel)"
            res["status"] = "pass"
            return res
        res["observed"] = "slide offset change=" + str(moved)
        res["status"] = "pass" if moved else "fail"
        return res

    if kind == "timer":
        fired = _timer_samples_vary(samples)
        if not fired:
            fired = (
                _opacity_changed(before, after)
                or _transform_changed(before, after)
                or _color_changed(before, after)
                or (
                    bool(before.get("childVisualSig"))
                    and bool(after.get("childVisualSig"))
                    and before.get("childVisualSig") != after.get("childVisualSig")
                )
            )
        res["observed"] = (
            f"{len(samples)} timer samples, visual state varied={fired}"
        )
        res["status"] = "pass" if fired else "fail"
        return res

    if kind == "click":
        fired = (
            _height_grew(before, after)
            or _opacity_changed(before, after)
            or _transform_changed(before, after)
            or _height_changed(before, after)
            or _child_changed(before, after)
        )
        res["observed"] = (
            f"height {before.get('height')} -> {after.get('height')}, "
            f"opacity {before.get('opacity')} -> {after.get('opacity')}"
        )
        res["status"] = "pass" if fired else "fail"
        return res

    if kind == "hover":
        if _is_reset_only_hover(entry):
            res["observed"] = "known-skip: reset-only hover rule has no runtime delta"
            res["status"] = "known-skip"
            return res
        fired = _any_visual_change(before, after)
        if not fired and _hover_descendant_measurement_declared(entry):
            fired = _child_changed(before, after)
        res["observed"] = "style change on hover=" + str(fired)
        res["status"] = "pass" if fired else "fail"
        return res

    # reveal / splash (and the default)
    # Deliberately NOT _top_moved: the driver scrollIntoView()s the element
    # before the AFTER snapshot, so a below-fold element's viewport `top`
    # ALWAYS changes regardless of animation. Counting it passed any static
    # element that merely sits below the fold (a loosening hole). Honest reveal
    # signal = opacity rise or transform change. Mirrors the scrub exclusion.
    fired = (
        _opacity_rose(before, after)
        or _transform_changed(before, after)
        or _child_revealed(before, after)
        or _stroke_drew(before, after)
        or (kind == "splash" and _opacity_changed(before, after))
    )
    # Fix M (loop-e2e-6): height/text channels, gated on the spec-declared
    # property (strokeDashoffset precedent). `before` is the PRE-sweep
    # pristine snapshot when available (driver prefers PRE), so the diff is
    # rest-state vs post-trigger, never mid-animation vs mid-animation.
    if not fired:
        prop = _anim_prop(entry)
        if "height" in prop and _child_height_grew(before, after):
            fired = True
            res["observed"] = (
                f"child height grew {before.get('childHeights')} -> "
                f"{after.get('childHeights')} (declared height property)"
            )
            res["status"] = "pass"
            return res
        # D17c (loop-nvti-0): the count-up declaration often lives in the
        # entry's id/trigger, not animation.property ("counter-digit-roll",
        # "digit columns translate to final value" with property
        # "transform:translateY per digit column"). Mine the whole spec-side
        # blob — still spec-declared input the impl cannot influence, so the
        # anti-bypass gate (undeclared text change ≠ motion) is preserved.
        decl_blob = (
            f"{prop} {str(entry.get('id', ''))} {str(entry.get('trigger', ''))}"
        ).lower()
        declares_count = (
            "count" in decl_blob
            or "digit" in decl_blob
            or "odometer" in decl_blob
            or "textcontent" in prop
        )
        if declares_count and _text_digest_changed(before, after):
            fired = True
            res["observed"] = (
                f"text digest changed {before.get('textDigest')!r} -> "
                f"{after.get('textDigest')!r} (declared count-up property)"
            )
            res["status"] = "pass"
            return res
    res["observed"] = (
        f"opacity {before.get('opacity')} -> {after.get('opacity')}, "
        f"transform {_norm_transform(before.get('transform'))} -> "
        f"{_norm_transform(after.get('transform'))}"
    )
    if before.get("strokeDashoffset") is not None or after.get("strokeDashoffset") is not None:
        res["observed"] += (
            f", strokeDashoffset {before.get('strokeDashoffset')} -> "
            f"{after.get('strokeDashoffset')}"
        )
    # Load-phase evidence is restricted to triggers that legitimately fire on
    # page load (splash overlays, autoplay, timers). IO/scroll reveals must
    # NOT pass via a DOMContentLoaded fade (codex review: bypass risk) — their
    # honest evidence is the pre-sweep initial snapshot delta.
    trigger = str(entry.get("trigger") or "").lower()
    load_trigger = any(
        t in trigger for t in ("load", "splash", "autoplay", "timer", "interval")
    ) and not any(t in trigger for t in ("scroll", "useinview", "io ", "io-", "intersection"))
    if not fired and load_trigger and _load_phase_fired(obs.get("loadSamples") or [], kind):
        res["observed"] += "; load-phase motion observed (fired before post-sweep probe)"
        res["status"] = "pass"
        return res
    # L-MEA-8: a one-shot IO reveal that finished during the settle mount sweep
    # reads flat in the main pass. Accept the driver's fresh-context re-probe
    # (pre-state out of view -> scrolled in and sampled) as the reveal firing.
    # Gated on an IO/inview/intersection trigger so it cannot rescue a non-IO
    # reveal, and it consumes only obs['revealProbe'] — never loadSamples — so
    # the load-phase IO exclusion above stays intact.
    io_reveal_trigger = any(
        t in trigger
        for t in ("inview", "in-view", "in view", "intersection", "io ", "io-", "viewport")
    )
    if (
        kind == "reveal"
        and io_reveal_trigger
        and _not_active_intersection_target(target)
    ):
        temporal = _reveal_probe_temporal_fired(obs.get("revealProbe"))
        res["observed"] += (
            "; :not(.active) intersection reveal requires temporal "
            f"fresh-probe motion={temporal}"
        )
        res["status"] = "pass" if temporal else "fail"
        return res
    if not fired and io_reveal_trigger and _reveal_probe_fired(obs.get("revealProbe")):
        res["observed"] += (
            "; fresh-context reveal re-probe observed pre-state -> settled motion"
        )
        res["status"] = "pass"
        return res
    res["status"] = "pass" if fired else "fail"
    return res


# ── roll-up ──────────────────────────────────────────────────────────────
def evaluate(spec: dict, observations: dict, asset_sub: dict,
             impl_url: str = "", ref_structure: dict | None = None) -> dict:
    skip_ids, unreasoned_skips = partition_skip_ids(asset_sub or {})
    # Captured-DOM token sets: an entry whose target is absent from the ref page
    # is reclassified known-skip (not fail) when its element is not found. When no
    # structure is provided the sets are empty and _target_absent_from_ref treats
    # a present-looking selector as absent ONLY if it has class/id tokens — so
    # guard on having captured something, else disable the reclassification.
    ref_classes: set[str] = set()
    ref_ids: set[str] = set()
    if isinstance(ref_structure, dict):
        _collect_dom_tokens(ref_structure, ref_classes, ref_ids, set())
    have_ref_dom = bool(ref_classes or ref_ids)
    transitions = (spec or {}).get("transitions") or []
    entries = []
    for t in transitions:
        if not isinstance(t, dict):
            continue
        eid = str(t.get("id", ""))
        obs = observations.get(eid) if isinstance(observations, dict) else None
        if not obs:
            obs = {"found": False, "before": {}, "after": {}}
        absent = have_ref_dom and _target_absent_from_ref(
            str(t.get("target", "")), ref_classes, ref_ids
        )
        entries.append(decide(t, obs, skip_ids, absent_from_ref=absent))

    fired = sum(1 for e in entries if e["status"] in ("pass", "degraded"))
    known = sum(1 for e in entries if e["status"] == "known-skip")
    failed = sum(1 for e in entries if e["status"] == "fail")
    # Honest abstention: the gate could not drive the transition (smooth-scroll
    # engine intercepted scroll). NOT a failure (don't penalise the clone for the
    # gate's own limitation) and NOT a fire (we did not verify it animates).
    unmeasurable = sum(1 for e in entries if e["status"] == "unmeasurable")
    total = len(entries)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "fail" if failed > 0 else "pass",
        "implUrl": impl_url,
        "total": total,
        "fired": fired,
        "known_skip": known,
        "failed": failed,
        "unmeasurable": unmeasurable,
        # Tracked debt: ids the gate could not verify. Consumed by the
        # verify-report motion-parity rollup so an "all green" closeout
        # cannot silently hide unverified motion.
        "unmeasurableIds": [
            e["id"] for e in entries if e["status"] == "unmeasurable"
        ],
        # Declared exemptions that stated no reason. Not honored (they were
        # measured normally) — reported so a rejected exemption is visible
        # rather than looking like it was never declared.
        "unreasonedSkipIds": sorted(unreasoned_skips),
        "entries": entries,
    }


def exit_ok(artifact: dict) -> bool:
    """Exit 0 only when no spec entry failed to fire (known-skips allowed)."""
    return int(artifact.get("failed", 0)) == 0


def summary_line(artifact: dict) -> str:
    fired = int(artifact.get("fired", 0))
    total = int(artifact.get("total", 0))
    known = int(artifact.get("known_skip", 0))
    failed = int(artifact.get("failed", 0))
    unmeasurable = int(artifact.get("unmeasurable", 0))
    extra = []
    if known:
        extra.append(f"{known} known-skip")
    if unmeasurable:
        extra.append(f"{unmeasurable} unmeasurable")
    if failed:
        extra.append(f"{failed} dead")
    suffix = f" ({', '.join(extra)})" if extra else ""
    # A fired count is not a fidelity result: this gate proves a MEASURED
    # runtime delta — that something moved — not that it moved the way the ref
    # does. A bar that snaps to full height before its section is on screen
    # still "fires". Say so, or the count gets read as trajectory parity.
    return (
        f"{fired}/{total} transitions fire{suffix}"
        " — fired only; trajectory fidelity is scroll-coverage / video-motion"
    )


# ── CLI: consumed by transition-fires-check.sh ───────────────────────────
def _load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    impl_url = ""
    if "--impl-url" in argv:
        i = argv.index("--impl-url")
        impl_url = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    if len(argv) < 4:
        sys.stderr.write(
            "usage: python -m ui_clone.gates.transition_fires "
            "<spec.json> <observations.json> <asset-substitution.json> "
            "<out.json> [--impl-url URL]\n"
        )
        return 2
    spec_path, obs_path, asset_path, out_path = argv[:4]
    spec = _load(spec_path) or {"transitions": []}
    observations = _load(obs_path) or {}
    asset_sub = _load(asset_path) or {}
    # structure.json sits beside transition-spec.json in the ref dir; used to
    # reclassify absent-from-page targets as known-skip instead of fail.
    ref_structure = _load(str(Path(spec_path).parent / "structure.json"))
    ref_structure = ref_structure if isinstance(ref_structure, dict) else None
    artifact = evaluate(
        spec, observations, asset_sub, impl_url=impl_url,
        ref_structure=ref_structure,
    )
    Path(out_path).write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print(summary_line(artifact))
    for e in artifact["entries"]:
        mark = {"pass": "✓", "degraded": "≈", "known-skip": "○", "fail": "✗"}.get(
            e["status"], "?")
        print(f"  {mark} {e['id']:<22} {e['kind']:<14} {e['status']:<10} "
              f"{e['observed']}")
    return 0 if exit_ok(artifact) else 1


if __name__ == "__main__":
    raise SystemExit(main())
