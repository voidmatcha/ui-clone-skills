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
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# Measurement thresholds — deliberately small so a real animation is caught,
# but above sub-pixel / float noise so a static element is not.
_OPACITY_EPS = 0.02
_TOP_EPS = 0.5      # px
_HEIGHT_EPS = 1.0   # px
_TIME_EPS = 0.05    # seconds (video currentTime advance)
_SCROLLABLE_DOC_PX = 300  # page must be at least this tall to expect scroll

_IDENTITY_TRANSFORMS = {"", "none", "matrix(1,0,0,1,0,0)", "matrix3d(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)"}

_EXPECTED = {
    "reveal": "opacity/transform advances when scrolled into view",
    "splash": "opacity/transform changes from initial to settled state on load",
    "scrub": "transform/position varies across the scroll range",
    "smooth-scroll": "page position advances under scroll (engine wrapper translates)",
    "carousel": "slide transform/offset changes over time",
    "hover": "computed style changes while hovered",
    "click": "target expands / opacity changes on click",
    "video": "currentTime advances after load",
    "webgl": "hero canvas renders non-blank pixels",
}


# ── trigger classification ───────────────────────────────────────────────
def classify(entry: dict) -> str:
    """Map a spec entry to the runtime-measurement kind. Disambiguates the
    several page-load variants (video / webgl / splash / smooth-scroll) by the
    animation type, since they share trigger=page-load."""
    trig = str(entry.get("trigger", "")).lower()
    anim = entry.get("animation")
    if isinstance(anim, dict):
        atype = str(anim.get("type", "")).lower()
        scrub_flag = bool(anim.get("scrub"))
    else:
        # Some extractors emit `animation` as a freeform description string
        # ("gsap.from y/opacity stagger ...") instead of a {type,...} dict.
        # Fold it into the classification blob so keyword matching still works
        # instead of crashing on `.get()` — a string-form spec otherwise takes
        # the whole gate down with 'str' has no attribute 'get'.
        atype = str(anim or "").lower()
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

    if "video" in blob:
        return "video"
    if ("webgl" in blob or "canvas" in blob
            or "webgl" in eid or "canvas" in eid or "canvas" in etgt):
        return "webgl"
    if "scrub" in blob or "progress" in blob or scrub_flag or "parallax" in blob or "parallax" in eid:
        return "scrub"
    if ("smooth-scroll" in blob or "smoothscroll" in blob or "lenis" in blob
            or "smooth-scroll" in eid or "smoothscroll" in eid
            or "lenis" in eid or "lenis" in etgt):
        return "smooth-scroll"
    if "carousel" in blob or "slider" in blob or "slideshow" in blob or "autoplay" in trig:
        return "carousel"
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
    # (juanmora cta-email-click-splittext: container static, child spans animate).
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


def _any_visual_change(before: dict, after: dict) -> bool:
    return (
        _opacity_changed(before, after)
        or _transform_changed(before, after)
        or _top_moved(before, after)
        or _height_changed(before, after)
        or _color_changed(before, after)
    )


def _samples_vary(samples: list) -> bool:
    # Scrub fires by animating transform/opacity across the scroll range.
    # Deliberately NOT viewport `top`: getBoundingClientRect().top moves on
    # ANY page scroll, so counting it would PASS an unimplemented scrub on
    # natural scroll alone (a loosening hole).
    if not samples:
        return False
    first = samples[0]
    f_t = _norm_transform(first.get("transform"))
    f_op = _f(first.get("opacity"))
    for s in samples[1:]:
        if _norm_transform(s.get("transform")) != f_t:
            return True
        op = _f(s.get("opacity"))
        if f_op is not None and op is not None and abs(op - f_op) > _OPACITY_EPS:
            return True
    return False


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
    # Two intercept modes: wrapper-mode (scrollY frozen, never advances) and
    # native-mode (scrollY advances but the scrub is bound to the engine's
    # virtual position a jump-scroll can't drive). Either way, on a scrollable
    # page, a flat transform is "couldn't drive", not "dead".
    return scrollable and (not advanced or engine)


# ── per-entry decision ───────────────────────────────────────────────────
def load_skip_ids(asset_sub: dict) -> set[str]:
    """Collect ids/targets that are legitimately exempt (paid-lib substitution
    or origin-locked WebGL) from asset-substitution.json. These count as
    KNOWN-SKIP — still listed, never a silent pass."""
    out: set[str] = set()
    if not isinstance(asset_sub, dict):
        return out
    for key in ("originLockedSkips", "substitutions", "skips"):
        for item in asset_sub.get(key) or []:
            if isinstance(item, str):
                out.add(item)
            elif isinstance(item, dict):
                for k in ("id", "target", "selector", "transitionId"):
                    v = item.get(k)
                    if v:
                        out.add(str(v))
    return out


def _anim_type(entry: dict) -> str:
    # `animation` may be a {type,...} dict or a freeform description string
    # (extractor variant). Never assume dict — both classify() and decide()
    # crashed on the string form (regression: adcker spec, exit 2).
    anim = entry.get("animation")
    if isinstance(anim, dict):
        return str(anim.get("type", ""))
    return str(anim or "")


def decide(entry: dict, obs: dict, skip_ids: set[str]) -> dict:
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
        res["status"] = "fail"
        res["observed"] = "element not found for target selector"
        return res

    before = obs.get("before") or {}
    after = obs.get("after") or {}
    samples = obs.get("samples") or []

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
            varied = _samples_vary(samples)
            if not varied and _scroll_blocked(samples):
                res["status"] = "unmeasurable"
                res["observed"] = (
                    "smooth-scroll engine intercepts programmatic scroll "
                    "(page did not advance); scrub unmeasurable, not dead"
                )
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
            or _samples_vary(samples)
            or _top_moved(before, after)
        )
        # scrollLeft offset (some sliders translate via scroll, not transform)
        sl_b, sl_a = _f(before.get("scrollLeft")), _f(after.get("scrollLeft"))
        if sl_b is not None and sl_a is not None and abs(sl_a - sl_b) > _TOP_EPS:
            moved = True
        res["observed"] = "slide offset change=" + str(moved)
        res["status"] = "pass" if moved else "fail"
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
        fired = _any_visual_change(before, after)
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
        or (kind == "splash" and _opacity_changed(before, after))
    )
    res["observed"] = (
        f"opacity {before.get('opacity')} -> {after.get('opacity')}, "
        f"transform {_norm_transform(before.get('transform'))} -> "
        f"{_norm_transform(after.get('transform'))}"
    )
    res["status"] = "pass" if fired else "fail"
    return res


# ── roll-up ──────────────────────────────────────────────────────────────
def evaluate(spec: dict, observations: dict, asset_sub: dict,
             impl_url: str = "") -> dict:
    skip_ids = load_skip_ids(asset_sub or {})
    transitions = (spec or {}).get("transitions") or []
    entries = []
    for t in transitions:
        if not isinstance(t, dict):
            continue
        eid = str(t.get("id", ""))
        obs = observations.get(eid) if isinstance(observations, dict) else None
        if not obs:
            obs = {"found": False, "before": {}, "after": {}}
        entries.append(decide(t, obs, skip_ids))

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
    return f"{fired}/{total} transitions fire{suffix}"


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
    artifact = evaluate(spec, observations, asset_sub, impl_url=impl_url)
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
