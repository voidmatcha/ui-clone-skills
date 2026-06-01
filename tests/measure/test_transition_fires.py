"""Unit tests for the per-entry transition-fires decision logic.

These exercise ui_clone.gates.transition_fires — the pure decision module that
the browser-driving transition-fires-check.sh feeds measured before/after
runtime state into. The whole point of the gate is FIX-NOT-LOOSEN: a PASS
requires a MEASURED runtime delta, and cannot be earned by a class name or a
`transition-` token (the hole the static coverage gate left open).

RED→GREEN anchors (mirrors the task's fixture trio):
  * a scroll-reveal whose target does NOT move (static, class present) → FAIL
  * a scroll-reveal whose target animates opacity 0→1 → PASS
  * a working click-disclosure whose spec id is not a string match → PASS
"""

from __future__ import annotations

from ui_clone.gates import transition_fires as tf


def _state(**kw: object) -> dict:
    base: dict[str, object] = {
        "opacity": None,
        "transform": None,
        "top": None,
        "height": None,
        "currentTime": None,
        "canvasCount": 0,
        "canvasNonBlank": False,
    }
    base.update(kw)
    return base


# ── scroll-reveal ────────────────────────────────────────────────────────
def test_scroll_reveal_animating_opacity_passes() -> None:
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".x"}
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="none"),
           "after": _state(opacity=1.0, transform="none")}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


def test_scroll_reveal_static_class_present_fails() -> None:
    # RED for the old name-match gate (class is present so it "passes"
    # statically); GREEN here — zero measured delta is a FAIL.
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".animate"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none", top=100.0),
           "after": _state(opacity=1.0, transform="none", top=100.0)}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_scroll_reveal_transform_advance_passes() -> None:
    entry = {"id": "t_reveal", "trigger": "scroll-reveal",
             "animation": {"type": "slide-in"}, "target": ".y"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="matrix(1, 0, 0, 1, 0, 40)"),
           "after": _state(opacity=1.0, transform="matrix(1, 0, 0, 1, 0, 0)")}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


def test_scroll_reveal_top_move_alone_fails() -> None:
    # LOOSENING HOLE: the driver snaps BEFORE at rest, then scrollIntoView()s
    # before the AFTER snap — which ALWAYS changes a below-fold element's
    # viewport top. Counting that as a reveal "fired" passes any static element
    # that merely sits below the fold (adcker highlight-reveal / custom-cursor
    # passed with opacity flat + transform identity). Honest reveal signal =
    # opacity rise or transform change; viewport top movement is scroll, not
    # animation. Mirrors the scrub gate's deliberate exclusion of `top`.
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".x"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none", top=3000.0),
           "after": _state(opacity=1.0, transform="none", top=400.0)}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_scroll_reveal_child_splittext_passes() -> None:
    # A splittext REVEAL animates child letters (opacity 0->1, transform offset
    # -> identity) while the container box stays flat — the same structural gap
    # Fix 45 closed for click. Child transform/opacity (getComputedStyle) change
    # only on animation, not scroll, so this is as safe as the container checks.
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "split-reveal"}, "target": ".headline"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none", top=900.0,
                            childSig="matrix(1,0,0,1,0,40)|0;none|0;none|0;"),
           "after": _state(opacity=1.0, transform="none", top=300.0,
                           childSig="matrix(1,0,0,1,0,0)|1;none|1;none|1;")}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_scroll_reveal_static_children_still_fails() -> None:
    # Guard the hole Fix 43 closed: top moved (scrollIntoView) but container AND
    # children are unchanged -> still fail. Child-sig must NOT reintroduce the
    # spurious pass for a genuinely static below-fold reveal.
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".x"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none", top=3000.0,
                            childSig="none|1;none|1;none|1;"),
           "after": _state(opacity=1.0, transform="none", top=400.0,
                           childSig="none|1;none|1;none|1;")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_scroll_reveal_child_transform_only_still_fails() -> None:
    # The false-positive the live adcker run caught: a child whose TRANSFORM
    # changes (scroll-parallax child / a cursor following the pointer) is NOT a
    # reveal. Child opacity stayed flat -> must fail, not pass. Guards against
    # reopening the Fix 43 loosening hole via child-sig.
    entry = {"id": "t_reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".x"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none", top=900.0,
                            childSig="matrix(1,0,0,1,0,40)|1;none|1;"),
           "after": _state(opacity=1.0, transform="none", top=300.0,
                           childSig="none|1;none|1;")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── click / disclosure (FAQ accordion) ───────────────────────────────────
def test_click_disclosure_height_grows_passes_even_without_name_match() -> None:
    # The spec id "t_faq_disclosure" is not a substring of anything in the
    # impl source; the static gate FAILs it. Behaviour is what matters: the
    # panel height grows on click → PASS.
    entry = {"id": "t_faq_disclosure", "trigger": "click",
             "animation": {"type": "accordion-expand"},
             "target": "details,[class*=faq]"}
    obs = {"found": True,
           "before": _state(opacity=1.0, height=0.0),
           "after": _state(opacity=1.0, height=320.0)}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


def test_click_disclosure_no_change_fails() -> None:
    entry = {"id": "t_faq", "trigger": "click",
             "animation": {"type": "accordion-expand"}, "target": ".faq"}
    obs = {"found": True,
           "before": _state(opacity=1.0, height=48.0),
           "after": _state(opacity=1.0, height=48.0)}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_click_splittext_child_animation_passes() -> None:
    # splittext-on-click animates CHILD spans (letters: opacity 0->1, transform
    # applied) while the container height/opacity/transform stay flat. The click
    # kind measured only the container and false-negatived a real animation
    # (juanmora cta-email-click-splittext: container static, child spans
    # none|0 -> matrix(1,0,0,1,0,0)|1). A measured child-signature delta fires.
    entry = {"id": "t_split", "trigger": "click",
             "animation": {"type": "click-stagger"}, "target": ".cta"}
    obs = {"found": True,
           "before": _state(height=179.0, opacity=1.0, transform="none",
                            childSig="none|0;none|0;none|0;"),
           "after": _state(height=179.0, opacity=1.0, transform="none",
                           childSig="matrix(1,0,0,1,0,0)|1;none|1;none|1;")}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_click_static_childsig_still_fails() -> None:
    # Guard the false-positive direction: identical child signature before/after
    # is NOT a fire (a click that animates nothing must still fail).
    entry = {"id": "t_c", "trigger": "click",
             "animation": {"type": "x"}, "target": ".c"}
    obs = {"found": True,
           "before": _state(height=48.0, opacity=1.0, childSig="none|1;none|1;"),
           "after": _state(height=48.0, opacity=1.0, childSig="none|1;none|1;")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── element resolution ───────────────────────────────────────────────────
def test_missing_element_fails_naming_entry() -> None:
    entry = {"id": "t_ghost", "trigger": "hover",
             "animation": {"type": "hover"}, "target": ".nope"}
    obs = {"found": False, "before": _state(), "after": _state()}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail"
    assert "t_ghost" in d["id"]
    assert "not found" in d["observed"].lower()


# ── video autoplay ───────────────────────────────────────────────────────
def test_video_currenttime_advance_passes() -> None:
    entry = {"id": "t_video", "trigger": "page-load",
             "animation": {"type": "video"}, "target": "video[autoplay]"}
    obs = {"found": True,
           "before": _state(currentTime=0.0),
           "after": _state(currentTime=1.4)}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_video_no_advance_fails() -> None:
    entry = {"id": "t_video", "trigger": "page-load",
             "animation": {"type": "video"}, "target": "video"}
    obs = {"found": True,
           "before": _state(currentTime=0.0),
           "after": _state(currentTime=0.0)}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── webgl / canvas hero ──────────────────────────────────────────────────
def test_webgl_nonblank_canvas_passes() -> None:
    entry = {"id": "t_hero_gl", "trigger": "page-load",
             "animation": {"type": "webgl"}, "target": "canvas"}
    obs = {"found": True,
           "before": _state(canvasCount=0, canvasNonBlank=False),
           "after": _state(canvasCount=1, canvasNonBlank=True)}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_webgl_blank_canvas_fails() -> None:
    entry = {"id": "t_hero_gl", "trigger": "page-load",
             "animation": {"type": "canvas hero"}, "target": "canvas"}
    obs = {"found": True,
           "before": _state(canvasCount=1, canvasNonBlank=False),
           "after": _state(canvasCount=1, canvasNonBlank=False)}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── scroll-scrub ─────────────────────────────────────────────────────────
def test_scroll_scrub_varying_samples_passes() -> None:
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "horizontal-scrub", "scrub": True},
             "target": ".scroller"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1.0, "top": 0.0},
               {"transform": "matrix(1, 0, 0, 1, -400, 0)", "opacity": 1.0, "top": 0.0},
               {"transform": "matrix(1, 0, 0, 1, -900, 0)", "opacity": 1.0, "top": 0.0},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_scroll_scrub_identical_samples_fails() -> None:
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "horizontal-scrub"}, "target": ".scroller"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1.0, "top": 0.0},
               {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1.0, "top": 0.0},
               {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1.0, "top": 0.0},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── scrub honest labeling: smooth-scroll engine intercepts programmatic scroll
# (Lenis/ScrollSmoother). The page never advances, so a flat transform is
# "couldn't drive the scroll", NOT "dead". Report unmeasurable, not fail —
# stops a false-negative without claiming the transition fired (no loosening).
def test_scroll_scrub_unmeasurable_when_scroll_blocked() -> None:
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 0, "docH": 4000},
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 0, "docH": 4000},
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 0, "docH": 4000},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "unmeasurable"


def test_scroll_scrub_flat_but_scroll_advanced_is_real_fail() -> None:
    # Guard against loosening: if the scroll genuinely ADVANCED (scrollY changed)
    # but the transform stayed flat, the scrub really did not fire -> fail, NOT
    # unmeasurable.
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": 800, "scrollY": 0, "docH": 4000},
               {"transform": "none", "opacity": 1.0, "top": 400, "scrollY": 600, "docH": 4000},
               {"transform": "none", "opacity": 1.0, "top": 100, "scrollY": 1200, "docH": 4000},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_scroll_scrub_native_mode_engine_advanced_is_unmeasurable() -> None:
    # Native-mode Lenis: window.scrollTo DOES advance scrollY, but the scrub is
    # bound to the engine's virtual position a jump-scroll can't drive, so the
    # transform stays flat. With a smooth engine detected, that's unmeasurable,
    # not dead (nivisgear.com t_text_scroll).
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000, "smoothEngine": True},
               {"transform": "none", "opacity": 1.0, "scrollY": 600, "docH": 4000, "smoothEngine": True},
               {"transform": "none", "opacity": 1.0, "scrollY": 1200, "docH": 4000, "smoothEngine": True},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "unmeasurable"


def test_scroll_scrub_blocked_but_short_page_is_not_unmeasurable() -> None:
    # Guard the other side: a SHORT (non-scrollable) page where scrollY stays 0
    # is not a smooth-scroll block — keep it a normal fail, not unmeasurable.
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 1},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 1},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 1},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_unmeasurable_does_not_fail_or_inflate_fire_count() -> None:
    spec = {"transitions": [{"id": "t_scrub", "trigger": "scroll-scrub",
                             "animation": {"type": "scrub"}, "target": ".x"}]}
    observations = {"t_scrub": {"found": True, "before": _state(), "after": _state(),
                    "samples": [
                        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000},
                        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000},
                    ]}}
    art = tf.evaluate(spec, observations, {}, impl_url="http://x")
    assert art["unmeasurable"] == 1
    assert art["fired"] == 0
    assert art["failed"] == 0
    assert tf.exit_ok(art) is True  # gate's own limitation must not fail the clone


# ── hover ────────────────────────────────────────────────────────────────
def test_hover_style_change_passes() -> None:
    entry = {"id": "t_hover", "trigger": "hover",
             "animation": {"type": "hover"}, "target": "a"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="matrix(1, 0, 0, 1, 0, 0)"),
           "after": _state(opacity=1.0, transform="matrix(1.05, 0, 0, 1.05, 0, 0)")}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_hover_no_change_fails() -> None:
    entry = {"id": "t_hover", "trigger": "hover",
             "animation": {"type": "hover"}, "target": "a"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none"),
           "after": _state(opacity=1.0, transform="none")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_hover_background_color_change_passes() -> None:
    # Real CSS :hover frequently changes ONLY color/background, leaving
    # opacity/transform/geometry untouched (e.g. nivisgear button-primary
    # bg rgb(191,238,22) -> rgb(217,253,48) on hover). The gate must credit
    # that measured color delta as fired, not false-negative a working hover
    # into "dead" because it only watched opacity/transform.
    entry = {"id": "t_hover", "trigger": "hover",
             "animation": {"type": "hover"}, "target": "button"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none",
                            backgroundColor="rgb(191, 238, 22)"),
           "after": _state(opacity=1.0, transform="none",
                            backgroundColor="rgb(217, 253, 48)")}
    assert tf.decide(entry, obs, set())["status"] == "pass"


# ── carousel autoplay ────────────────────────────────────────────────────
def test_carousel_transform_change_passes() -> None:
    entry = {"id": "t_carousel", "trigger": "autoplay",
             "animation": {"type": "carousel"}, "target": ".slideshow"}
    obs = {"found": True,
           "before": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),
           "after": _state(transform="matrix(1, 0, 0, 1, -600, 0)")}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_carousel_static_fails() -> None:
    entry = {"id": "t_carousel", "trigger": "autoplay",
             "animation": {"type": "carousel"}, "target": ".slideshow"}
    obs = {"found": True,
           "before": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),
           "after": _state(transform="matrix(1, 0, 0, 1, 0, 0)")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── smooth-scroll (degraded) ─────────────────────────────────────────────
def test_smooth_scroll_scrolls_but_no_engine_is_degraded() -> None:
    entry = {"id": "t_smooth", "trigger": "page-load",
             "animation": {"type": "smooth-scroll", "engine": "ScrollSmoother"},
             "target": "body"}
    # Page scrolled (top advanced) but no transform wrapper signature →
    # it moved, but not faithfully → degraded (fires, does not fail gate).
    obs = {"found": True,
           "before": _state(top=0.0, transform="none"),
           "after": _state(top=-1200.0, transform="none")}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "degraded", d


def test_smooth_scroll_no_movement_fails() -> None:
    entry = {"id": "t_smooth", "trigger": "page-load",
             "animation": {"type": "smooth-scroll"}, "target": "body"}
    obs = {"found": True,
           "before": _state(top=0.0, transform="none"),
           "after": _state(top=0.0, transform="none")}
    assert tf.decide(entry, obs, set())["status"] == "fail"


# ── known-skip via asset-substitution ────────────────────────────────────
def test_known_skip_by_id_is_not_a_failure() -> None:
    entry = {"id": "t_paid_lib", "trigger": "autoplay",
             "animation": {"type": "carousel"}, "target": ".paid"}
    obs = {"found": False, "before": _state(), "after": _state()}
    d = tf.decide(entry, obs, {"t_paid_lib"})
    assert d["status"] == "known-skip", d


def test_load_skip_ids_reads_origin_locked_and_substitutions() -> None:
    asset_sub = {
        "originLockedSkips": [{"id": "t_locked"}, {"target": ".gl"}],
        "substitutions": [{"id": "t_sub"}],
    }
    ids = tf.load_skip_ids(asset_sub)
    assert "t_locked" in ids
    assert ".gl" in ids
    assert "t_sub" in ids


# ── evaluate() roll-up + exit logic ──────────────────────────────────────
def test_evaluate_all_fire_exits_ok() -> None:
    spec = {"transitions": [
        {"id": "a", "trigger": "scroll-into-view",
         "animation": {"type": "fade-up"}, "target": ".a"},
    ]}
    observations = {"a": {"found": True,
                          "before": _state(opacity=0.0),
                          "after": _state(opacity=1.0)}}
    art = tf.evaluate(spec, observations, {}, impl_url="http://x")
    assert art["status"] == "pass"
    assert art["failed"] == 0
    assert art["fired"] == 1
    assert tf.exit_ok(art) is True


def test_evaluate_one_dead_entry_fails_gate() -> None:
    spec = {"transitions": [
        {"id": "a", "trigger": "scroll-into-view",
         "animation": {"type": "fade-up"}, "target": ".a"},
        {"id": "b", "trigger": "scroll-scrub",
         "animation": {"type": "horizontal-scrub"}, "target": ".b"},
    ]}
    observations = {
        "a": {"found": True, "before": _state(opacity=0.0),
              "after": _state(opacity=1.0)},
        "b": {"found": True, "before": _state(), "after": _state(),
              "samples": [
                  {"transform": "none", "opacity": 1.0, "top": 0.0},
                  {"transform": "none", "opacity": 1.0, "top": 0.0},
              ]},
    }
    art = tf.evaluate(spec, observations, {}, impl_url="http://x")
    assert art["status"] == "fail"
    assert art["failed"] == 1
    assert tf.exit_ok(art) is False


def test_evaluate_known_skip_listed_not_failed() -> None:
    spec = {"transitions": [
        {"id": "locked", "trigger": "page-load",
         "animation": {"type": "webgl"}, "target": "canvas"},
    ]}
    asset_sub = {"originLockedSkips": [{"id": "locked"}]}
    observations = {"locked": {"found": False, "before": _state(),
                               "after": _state()}}
    art = tf.evaluate(spec, observations, asset_sub, impl_url="http://x")
    assert art["known_skip"] == 1
    assert art["failed"] == 0
    assert tf.exit_ok(art) is True
    # still listed in entries
    assert any(e["id"] == "locked" and e["status"] == "known-skip"
               for e in art["entries"])


def test_evaluate_empty_spec_is_not_a_failure() -> None:
    art = tf.evaluate({"transitions": []}, {}, {}, impl_url="http://x")
    assert art["total"] == 0
    assert tf.exit_ok(art) is True


def test_summary_line_counts_fires() -> None:
    art = {"total": 8, "fired": 1, "known_skip": 0, "failed": 7}
    line = tf.summary_line(art)
    assert "1/8" in line
    assert "fire" in line


# ── classify() robustness: animation as a freeform string ──────────────────
# Some extractors emit `animation` as a description string ("gsap.from y/opacity
# stagger ...") instead of a {type,...} dict. classify() must fold the string
# into keyword matching, NOT crash on `.get()` — a regression that took the
# whole gate down (exit 2) on the adcker spec, blocking every entry.
def test_classify_string_animation_does_not_crash() -> None:
    entry = {"id": "video-play", "trigger": "click",
             "animation": "native video play on thumbnail click"}
    assert tf.classify(entry) == "video"


def test_classify_string_animation_lenis_is_smooth_scroll() -> None:
    entry = {"id": "smooth-scroll", "trigger": "scroll",
             "animation": "lenis.raf loop; wrapper/content translate"}
    assert tf.classify(entry) == "smooth-scroll"


def test_classify_string_animation_unknown_falls_back_to_reveal() -> None:
    entry = {"id": "custom-cursor", "trigger": "mousemove",
             "animation": "crosshair cursor follow"}
    assert tf.classify(entry) == "reveal"


def test_classify_none_animation_does_not_crash() -> None:
    assert tf.classify({"id": "x", "trigger": "page-load"}) == "reveal"


# ── classify() id/target boost: kind signal lives in id/target, not just type ──
# Running the gate on real sites showed entries MISCLASSIFIED as the fallback
# "reveal" because trigger+animation.type carried no keyword — the signal was in
# the id/target (bg-canvas, lenis, scroll-parallax). reveal's single-scroll
# measurement then false-negatives a real canvas/scrub/smooth-scroll. Boost
# webgl/scrub/smooth-scroll from id+target. Deliberately NOT video (would
# override a confident hover/click/splash whose element merely contains a
# <video> — e.g. nivisgear t_hero_splash).
def test_classify_canvas_in_target_is_webgl() -> None:
    entry = {"id": "bg-canvas-animation", "trigger": "page-load",
             "target": ".bg-canvas"}
    assert tf.classify(entry) == "webgl"


def test_classify_lenis_in_target_is_smooth_scroll() -> None:
    entry = {"id": "lenis-smooth-scroll", "trigger": "page-load",
             "target": "html.lenis"}
    assert tf.classify(entry) == "smooth-scroll"


def test_classify_parallax_is_scrub() -> None:
    entry = {"id": "patch-scroll-parallax", "trigger": "scroll",
             "animation": "scroll-parallax-y", "target": ".patch"}
    assert tf.classify(entry) == "scrub"


def test_classify_canvas_and_video_target_prefers_webgl() -> None:
    # unicorn-hero-canvas: target lists both a canvas and a video wrapper. Must
    # stay webgl (so Fix 39 blank-canvas detection applies), NOT video.
    entry = {"id": "unicorn-hero-canvas", "trigger": "mount",
             "target": "canvas, [data-us-project], .intro_video-wrapper"}
    assert tf.classify(entry) == "webgl"


def test_classify_video_in_target_does_not_override_confident_kind() -> None:
    # A hover/splash whose element merely CONTAINS a <video> must keep its
    # confident kind — id/target video signal must not promote it to video
    # (regression guard: nivisgear t_hero_splash -> would break the 8/8 proof).
    splash = {"id": "t_hero_splash", "trigger": "page-load",
              "animation": {"type": "scale-in"}, "target": "video.hero-index-video"}
    assert tf.classify(splash) == "splash"
    hover = {"id": "video-preview-hover", "trigger": "hover",
             "target": "#preview video"}
    assert tf.classify(hover) == "hover"


def test_decide_string_animation_does_not_crash() -> None:
    # The second `.get("type")` site (decide's res["type"]) must also tolerate a
    # string-form animation, not just classify() — the gate crashed here after
    # classify() alone was fixed.
    entry = {"id": "smooth-scroll", "trigger": "scroll",
             "animation": "lenis.raf loop; wrapper/content translate",
             "target": ".js-lenis-wrapper"}
    obs = {"found": True,
           "before": _state(top=0), "after": _state(top=-200)}
    res = tf.decide(entry, obs, set())
    assert isinstance(res["status"], str)
