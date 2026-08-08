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

from pathlib import Path

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


def test_fade_carousel_passes_on_active_index_channel() -> None:
    """A2: a fade carousel holds its wrapper transform at identity, so the
    transform channel never varies — but the probe's effect-agnostic carousel
    fingerprint (active index + slide opacity vector) changes when it advances.
    That channel alone must pass the carousel verdict."""
    entry = {"id": "swiper-hero", "trigger": "auto",
             "animation": {"type": "carousel"}, "target": ".swiper-wrapper"}
    obs = {"found": True,
           "before": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),
           "after": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),  # identity, unchanged
           "carousel": {"before": "0|matrix(1, 0, 0, 1, 0, 0)|100,0,0,",
                        "after": "1|matrix(1, 0, 0, 1, 0, 0)|0,100,0,"}}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


def test_swiper_type_and_trigger_use_carousel_probe() -> None:
    """Swiper vocabulary must select the carousel fingerprint channel."""
    entry = {
        "id": "hero-swiper",
        "trigger": "swiper-next",
        "animation": {"type": "swiper"},
        "target": '.swiper[data-ui-clone-swiper="0"]',
    }
    obs = {
        "found": True,
        "before": _state(transform="none"),
        "after": _state(transform="none"),
        "carousel": {"before": "0|none|100,0,", "after": "1|none|0,100,"},
    }
    assert tf.classify(entry) == "carousel"
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


def test_hover_font_weight_change_is_visual_evidence() -> None:
    entry = {
        "id": "header-link-hover",
        "trigger": "hover",
        "animation": {"type": "css-hover", "property": "renderedPixels"},
        "target": ".nav__link",
    }
    obs = {
        "found": True,
        "before": _state(fontWeight="400"),
        "after": _state(fontWeight="600"),
    }

    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_hover_pseudo_change_is_visual_evidence() -> None:
    entry = {
        "id": "header-arrow-hover",
        "trigger": "hover",
        "animation": {"type": "css-hover", "property": "renderedPixels"},
        "target": ".nav__item.is-arrow .nav__link",
    }
    obs = {
        "found": True,
        "before": _state(pseudoAfter="opacity:0|transform:none"),
        "after": _state(pseudoAfter="opacity:1|transform:none"),
    }

    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_obs_merge_forwards_carousel_fingerprint() -> None:
    """T-4 regression guard: the phase-2 probe captures a carousel fingerprint on
    the after-record, and the verdict reads obs['carousel'] — but the obs-merge in
    transition-fires-check.sh must actually carry it across, or the fade-carousel
    channel is silently dead (the verdict passed its unit tests on a hand-built
    obs while the live wiring dropped the field). The merge is an embedded Python
    heredoc (not importable), so this is a source-level guard on that seam."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2]
              / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh")
    src = script.read_text(encoding="utf-8")
    # the obs[...] = { ... } merge must forward the after-record's carousel field
    assert '"carousel": a.get("carousel")' in src, (
        "the obs-merge must carry the carousel fingerprint from the after-record "
        "into the verdict input, or the fade-carousel channel is dead (T-4 bug)"
    )


def test_dead_carousel_identical_fingerprint_fails() -> None:
    """The fingerprint channel cannot false-pass a dead carousel: an unchanged
    active index + opacity vector (nothing advanced) stays fail."""
    entry = {"id": "swiper-dead", "trigger": "auto",
             "animation": {"type": "carousel"}, "target": ".swiper-wrapper"}
    same = "0|matrix(1, 0, 0, 1, 0, 0)|100,0,0,"
    obs = {"found": True,
           "before": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),
           "after": _state(transform="matrix(1, 0, 0, 1, 0, 0)"),
           "carousel": {"before": same, "after": same}}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


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


def test_css_sticky_non_sticky_impl_fails_even_with_reveal_delta() -> None:
    entry = {
        "id": "sticky-header",
        "trigger": "scroll",
        "animation": {
            "type": "css-sticky",
            "property": "position, top",
            "stickyRangeH": 136,
        },
        "target": ".header",
    }
    obs = {
        "found": True,
        "before": _state(
            opacity=0.0, position="relative", cssTop="0px", top=0.0
        ),
        "after": _state(
            opacity=1.0, position="relative", cssTop="0px", top=0.0
        ),
        "samples": [
            {"position": "relative", "cssTop": "0px", "top": 0.0, "scrollY": 50.0},
            {"position": "relative", "cssTop": "0px", "top": 0.0, "scrollY": 186.0},
        ],
    }

    d = tf.decide(entry, obs, set())

    assert d["kind"] == "sticky"
    assert d["status"] == "fail", d
    assert "not sticky" in d["observed"]


def test_css_sticky_pinned_across_declared_range_passes() -> None:
    entry = {
        "id": "sticky-header",
        "trigger": "scroll",
        "animation": {
            "type": "css-sticky",
            "property": "position, top",
            "from": "position: sticky; top: 12px",
            "stickyRangeH": 136,
        },
        "target": ".header",
    }
    obs = {
        "found": True,
        "before": _state(position="sticky", cssTop="12px", top=12.0),
        "after": _state(position="sticky", cssTop="12px", top=12.0),
        "samples": [
            {"position": "sticky", "cssTop": "12px", "top": 12.0, "scrollY": 80.0},
            {"position": "sticky", "cssTop": "12px", "top": 12.0, "scrollY": 216.0},
        ],
    }

    d = tf.decide(entry, obs, set())

    assert d["kind"] == "sticky"
    assert d["status"] == "pass", d
    assert "pinned across 136.0px" in d["observed"]


def test_css_sticky_range_excludes_box_and_insets_from_container_height() -> None:
    """A 200px container does not provide 200px of sticky pin travel.

    For a container below the document start, the 64px sticky box leaves 136px
    of pin travel. The top inset shifts both pin start and end; it does not turn
    the full container height into achievable travel.
    """
    entry = {
        "id": "sticky-nav",
        "trigger": "scroll",
        "animation": {
            "type": "css-sticky",
            "from": "position: sticky; top: 8px",
            "stickyContainerH": 200,
            "stickyRangeH": 136,
        },
        "target": ".nav",
    }
    obs = {
        "found": True,
        "before": _state(position="sticky", cssTop="8px", top=8.0),
        "after": _state(position="sticky", cssTop="8px", top=8.0),
        "samples": [
            {"position": "sticky", "cssTop": "8px", "top": 8.0, "scrollY": 40.0},
            {"position": "sticky", "cssTop": "8px", "top": 8.0, "scrollY": 176.0},
        ],
    }

    decision = tf.decide(entry, obs, set())

    assert decision["status"] == "pass", decision
    assert "expected 136.0px" in decision["observed"]


def test_css_sticky_samples_must_still_have_sticky_position() -> None:
    entry = {
        "id": "sticky-nav",
        "trigger": "scroll",
        "animation": {
            "type": "css-sticky",
            "from": "position: sticky; top: 8px",
            "stickyRangeH": 40,
        },
        "target": ".nav",
    }
    obs = {
        "found": True,
        "before": _state(position="sticky", cssTop="8px", top=8.0),
        "after": _state(position="relative", cssTop="8px", top=8.0),
        "samples": [
            {"position": "relative", "cssTop": "8px", "top": 8.0, "scrollY": 40.0},
            {"position": "relative", "cssTop": "8px", "top": 8.0, "scrollY": 80.0},
        ],
    }

    decision = tf.decide(entry, obs, set())

    assert decision["status"] == "fail", decision
    assert "no sampled pinning" in decision["observed"]


def test_css_sticky_live_probe_emits_judge_fields_and_samples() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "transition-fires-check.sh"
    )
    src = script.read_text(encoding="utf-8")
    producer = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "extract-section-map.sh"
    ).read_text(encoding="utf-8")

    assert '"stickyRangeH": (' in src
    assert '"stickyContainerH": (' in src
    assert "containerBottom - stickyBoxH - topInset" in src
    assert "const achievableRange = Math.max(0, achievableEnd - start);" in src
    assert "stickyContainerH: (() => {" in producer
    assert "containerTop + containerH - stickyRect.height - topInset" in producer
    assert "rangeH: Math.max(0, Math.round(pinEnd - pinStart))" in producer
    assert "if (e.kind === 'sticky') { s.position = cs.position; s.cssTop = cs.top; }" in src
    assert "} else if (e.kind === 'scrub') {" in src
    assert "for (const p of [0, 0.25, 0.5, 0.75, 1])" in src
    assert (
        "samples.push({ position: cs.position, cssTop: cs.top, "
        "top: rr.top, scrollY: window.scrollY });"
    ) in src
    assert "rec.samples = samples;" in src
    assert "rec.after = snap(el, e);" in src
    assert '"samples": a.get("samples", []) or []' in src


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


# ── timer-driven remounts ────────────────────────────────────────────────
def test_timer_card_flip_uses_timer_probe() -> None:
    entry = {
        "id": "color-palette-cycle-phase",
        "trigger": "intersection-observer timer",
        "animation": {"type": "timer-driven card flip"},
        "target": ".flip-card-inner",
    }
    assert tf.classify(entry) == "timer"


def test_timer_varying_remount_visual_signature_passes() -> None:
    entry = {
        "id": "palette-cycle",
        "trigger": "intersection timer",
        "animation": {"type": "card flip"},
        "target": ".flip-card-inner",
    }
    obs = {
        "found": True,
        "before": _state(),
        "after": _state(),
        "samples": [
            _state(
                transform="matrix(1, 0, 0, 1, 0, 0)",
                childVisualSig="none|1|rgb(0,0,0)|rgb(255,255,255);",
            ),
            _state(
                transform="matrix3d(-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1)",
                childVisualSig="none|1|rgb(255,255,255)|rgb(0,0,0);",
            ),
        ],
    }
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_timer_flat_samples_fail() -> None:
    entry = {
        "id": "palette-cycle",
        "trigger": "interval timer",
        "animation": {"type": "card flip"},
        "target": ".flip-card-inner",
    }
    flat = _state(
        transform="none",
        opacity=1,
        childVisualSig="none|1|rgb(0,0,0)|rgb(255,255,255);",
    )
    obs = {
        "found": True,
        "before": flat,
        "after": flat,
        "samples": [dict(flat), dict(flat), dict(flat)],
    }
    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_timer_probe_requeries_remounted_target_and_canvas_samples_grid() -> None:
    src = Path(
        "skills/visual-debug/scripts/transition-fires-check.sh"
    ).read_text()
    assert "current = resolveEntry(e, current)" in src
    assert "const live = resolveEntry(e, null)" in src
    assert "for (const [x, y, size] of sampleTiles(c.width, c.height))" in src
    assert "getImageData(x, y, size, size)" in src


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


def test_scroll_class_toggle_classifies_and_fires_as_scrub() -> None:
    """A scroll threshold class toggle is scroll state, not an IO reveal."""
    entry = {
        "id": "scroll-header-shadow-threshold",
        "trigger": "scroll",
        "target": "header.style_header__tjhHk",
        "animation": {
            "type": "scroll-state-class-toggle",
            "property": "box-shadow,className",
            "from": {"className": "style_header__tjhHk"},
            "to": {
                "className": (
                    "style_header__tjhHk style_header__shadow__9G5rH"
                )
            },
        },
    }
    assert tf.classify(entry) == "scrub"
    observations = {
        "found": True,
        "before": _state(),
        "after": _state(),
        "samples": [
            {
                "transform": "none",
                "opacity": 1,
                "cls": "style_header__tjhHk",
                "scrollY": 0,
                "docH": 4000,
            },
            {
                "transform": "none",
                "opacity": 1,
                "cls": (
                    "style_header__tjhHk style_header__shadow__9G5rH"
                ),
                "scrollY": 100,
                "docH": 4000,
            },
        ],
    }
    assert tf.decide(entry, observations, set())["status"] == "pass"


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


def test_hover_descendant_change_requires_explicit_measurement_scope() -> None:
    entry = {
        "id": "t_hover",
        "trigger": "hover",
        "animation": {"type": "css-hover"},
        "target": ".nav-item",
    }
    obs = {
        "found": True,
        "before": _state(opacity=1.0, transform="none", childSig="none|0;"),
        "after": _state(opacity=1.0, transform="none", childSig="none|1;"),
    }

    assert tf.decide(entry, obs, set())["status"] == "fail"


def test_hover_descendant_change_passes_when_measurement_scope_is_declared() -> None:
    entry = {
        "id": "t_hover",
        "trigger": "hover",
        "animation": {
            "type": "css-hover",
            "measurement": "target-and-descendants",
        },
        "target": ".nav-item",
    }
    obs = {
        "found": True,
        "before": _state(opacity=1.0, transform="none", childSig="none|0;"),
        "after": _state(opacity=1.0, transform="none", childSig="none|1;"),
    }

    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_hover_snapshot_includes_descendant_signature() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
    source = script.read_text(encoding="utf-8")

    assert (
        "e.kind === 'hover' || e.kind === 'click' || "
        "e.kind === 'reveal' || e.kind === 'splash'"
    ) in source


def test_reset_only_hover_rule_is_known_skip() -> None:
    entry = {
        "id": "auto-hover-0",
        "trigger": "hover",
        "target": "a",
        "animation": {
            "type": "css-hover",
            "cssText": "a:hover {text-decoration:none}",
        },
    }
    obs = {"found": True, "before": _state(), "after": _state()}
    result = tf.decide(entry, obs, set())
    assert result["status"] == "known-skip"
    assert "reset-only hover" in result["observed"]


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
        "originLockedSkips": [
            {"id": "t_locked", "reason": "origin-locked WebGL"},
            {"target": ".gl", "reason": "origin-locked WebGL sibling canvas"},
        ],
        "substitutions": [{"id": "t_sub", "reason": "paid lib substituted"}],
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
    asset_sub = {"originLockedSkips": [
        {"id": "locked", "reason": "origin-locked WebGL: shader served only "
                                   "to the ref origin"},
    ]}
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


def test_transition_fires_hover_patch_preserves_synthetic_js_delta() -> None:
    """The browser shell patch must not erase a JS hover-handler delta with a
    flat real-pointer snapshot, and comma selectors must be tried as candidate
    selectors before CDP hover.
    """
    root = Path(__file__).resolve().parents[2]
    script = root / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
    text = script.read_text(encoding="utf-8")

    assert "raw = [p.strip()" in text
    # F6: each comma candidate is expanded to [class*=]/hash-strip fallbacks so a
    # hashed CSS-module hover target resolves under the real CDP pointer too.
    assert "_fallbacks(c)" in text
    assert "base64.b64encode(json.dumps(candidates)" in text
    assert "while IFS= read -r HSEL" in text
    hover_target = "[data-tf-hover-target='$HIDX']"
    scroll_into_view = (
        f'agent-browser --session "$SESSION" scrollintoview "{hover_target}"'
    )
    layout_wait = 'agent-browser --session "$SESSION" wait 250'
    pointer_hover = f'agent-browser --session "$SESSION" hover "{hover_target}"'
    assert text.index(scroll_into_view) < text.index(layout_wait) < text.index(pointer_hover)
    assert "def style_changed" in text
    assert "boxShadow" in text
    assert "filter" in text
    assert "style_changed(current_after, baseline) and not style_changed(pointer_after, baseline)" in text


def test_hover_patch_targets_largest_visible_match() -> None:
    """Shared selectors must not resolve to a 1px skip link over visible nav."""
    root = Path(__file__).resolve().parents[2]
    script = root / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
    text = script.read_text(encoding="utf-8")

    assert "data-tf-hover-target" in text
    assert "b.width * b.height - a.width * a.height" in text
    assert 'hover "[data-tf-hover-target=\'$HIDX\']"' in text


def test_scrub_probe_samples_svg_descendant_motion() -> None:
    """Framer scrubs frequently animate SVG/g descendants, not the root."""
    root = Path(__file__).resolve().parents[2]
    script = root / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
    text = script.read_text(encoding="utf-8")

    assert "span,div,em,b,i,p,a,img,video,svg,g,path" in text


def test_transition_fires_resets_pointer_before_hover_baseline() -> None:
    """A reused agent-browser daemon may leave the pointer over a target.

    The before snapshot must start outside ``:hover`` or a live CSS hover reads
    hovered -> hovered and is falsely classified as dead.
    """
    root = Path(__file__).resolve().parents[2]
    script = root / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh"
    text = script.read_text(encoding="utf-8")

    reset = 'agent-browser --session "$SESSION" mouse move -100 -100'
    assert reset in text
    assert text.index(reset) < text.index('BEFORE_RAW=$(agent-browser --session "$SESSION" eval "$PHASE1"')


# ── wheel re-probe: when the smooth-scroll engine blocked scrollTo, the gate
# re-drives the page with real wheel events. Those samples carry
# wheelDriven=true and get measured verdicts: varied -> pass; flat while the
# element moved through the viewport -> dead; undriveable -> unmeasurable.
def test_wheel_reprobe_varied_samples_pass() -> None:
    entry = {"id": "t_wheel", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 0.2, "top": 900, "wheelDriven": True},
               {"transform": "none", "opacity": 0.7, "top": 400, "wheelDriven": True},
               {"transform": "none", "opacity": 1.0, "top": 50, "wheelDriven": True},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "pass"


def test_wheel_reprobe_flat_but_page_moved_is_dead() -> None:
    entry = {"id": "t_wheel", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": 900, "wheelDriven": True},
               {"transform": "none", "opacity": 1.0, "top": 400, "wheelDriven": True},
               {"transform": "none", "opacity": 1.0, "top": 50, "wheelDriven": True},
           ]}
    res = tf.decide(entry, obs, set())
    assert res["status"] == "fail"
    assert "dead" in res["observed"]


def test_wheel_reprobe_undriveable_stays_unmeasurable() -> None:
    entry = {"id": "t_wheel", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": 200, "wheelDriven": True},
               {"transform": "none", "opacity": 1.0, "top": 200, "wheelDriven": True},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "unmeasurable"


def test_wheel_reprobe_fixed_element_scroll_advanced_is_dead() -> None:
    # A position:fixed scrub target (floating nav, scroll-progress dot) keeps a
    # constant rect.top even as the page scrolls, so the top-delta page-moved
    # check alone mislabels a dead scrub "unmeasurable". The engine-driven
    # re-probe (window.__lenis.scrollTo) records scrollY: when scrollY genuinely
    # advanced but transform/opacity stayed flat, the scrub is DEAD, not
    # unmeasurable (realfood .nav-module__nav / __dot).
    entry = {"id": "t_fixed", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".floating-nav"}
    # Real Lenis: smoothEngine present AND drivable via __lenis.scrollTo, so the
    # engine's virtual scroll genuinely advanced — a flat fixed scrub is dead.
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": -144, "scrollY": 0,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": True},
               {"transform": "none", "opacity": 1.0, "top": -144, "scrollY": 1400,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": True},
               {"transform": "none", "opacity": 1.0, "top": -144, "scrollY": 5000,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": True},
           ]}
    res = tf.decide(entry, obs, set())
    assert res["status"] == "fail"
    assert "dead" in res["observed"]


def test_wheel_reprobe_smooth_engine_not_drivable_stays_unmeasurable() -> None:
    # A smooth engine is present but exposes NO drivable API (__lenis/ScrollSmoother
    # absent), so the per-step drive fell back to native window.scrollTo: scrollY
    # rose without advancing the engine's virtual scroll. A flat scrub here is
    # UNMEASURABLE, not dead — the symmetric guard to the fixed-element dead case.
    entry = {"id": "t_native", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 0,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": False},
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 1400,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": False},
               {"transform": "none", "opacity": 1.0, "top": 200, "scrollY": 5000,
                "wheelDriven": True, "smoothEngine": True, "engineDriven": False},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "unmeasurable"


def test_evaluate_exposes_unmeasurable_ids_as_tracked_debt() -> None:
    spec = {"transitions": [
        {"id": "t_ok", "trigger": "scroll-scrub",
         "animation": {"type": "scrub"}, "target": ".a"},
        {"id": "t_blocked", "trigger": "scroll-scrub",
         "animation": {"type": "scrub"}, "target": ".b"},
    ]}
    observations = {
        "t_ok": {"found": True, "before": _state(), "after": _state(),
                 "samples": [
                     {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1.0},
                     {"transform": "matrix(1, 0, 0, 1, -300, 0)", "opacity": 1.0},
                 ]},
        "t_blocked": {"found": True, "before": _state(), "after": _state(),
                      "samples": [
                          {"transform": "none", "opacity": 1.0, "top": 200,
                           "scrollY": 0, "docH": 4000},
                          {"transform": "none", "opacity": 1.0, "top": 200,
                           "scrollY": 0, "docH": 4000},
                      ]},
    }
    artifact = tf.evaluate(spec, observations, {})
    assert artifact["status"] == "pass"
    assert artifact["unmeasurable"] == 1
    assert artifact["unmeasurableIds"] == ["t_blocked"]


# ── Declared-skip exemptions must state a reason ─────────────────────────
# load_skip_ids() honored ANY id under originLockedSkips/substitutions/skips
# with no reason and no cap, so the one block-severity motion gate could be
# passed by listing every unfired transition. Image substitutions in the same
# artifact are already corroborated against download-log.json failures; the
# transition channel had no equivalent. An exemption must say WHY.
def test_load_skip_ids_rejects_reasonless_dict() -> None:
    ids = tf.load_skip_ids({"originLockedSkips": [{"id": "t_locked"}]})
    assert "t_locked" not in ids


def test_load_skip_ids_rejects_bare_string() -> None:
    # A bare string cannot carry a reason, so it can never be a justified skip.
    ids = tf.load_skip_ids({"skips": ["t_bare"]})
    assert "t_bare" not in ids


def test_load_skip_ids_rejects_blank_reason() -> None:
    ids = tf.load_skip_ids({"skips": [{"id": "t_x", "reason": "   "}]})
    assert "t_x" not in ids


def test_load_skip_ids_honors_reasoned_entry() -> None:
    ids = tf.load_skip_ids({
        "originLockedSkips": [
            {"id": "t_locked", "reason": "origin-locked WebGL: shader source "
                                         "is served only to the ref origin"},
            {"target": ".gl", "reason": "same origin lock, sibling canvas"},
        ],
        "substitutions": [{"id": "t_sub", "reason": "paid lib substituted"}],
    })
    assert {"t_locked", ".gl", "t_sub"} <= ids


def test_evaluate_reasonless_skip_does_not_exempt() -> None:
    # End-to-end: a reasonless declaration must not convert a dead transition
    # into a known-skip. It stays measured, so the gate fails.
    spec = {"transitions": [
        {"id": "locked", "trigger": "page-load",
         "animation": {"type": "webgl"}, "target": "canvas"},
    ]}
    observations = {"locked": {"found": False, "before": _state(),
                               "after": _state()}}
    art = tf.evaluate(spec, observations, {"originLockedSkips": [{"id": "locked"}]},
                      impl_url="http://x")
    assert art["known_skip"] == 0
    assert art["failed"] == 1
    assert tf.exit_ok(art) is False


def test_evaluate_rejected_skips_are_reported_not_silent() -> None:
    art = tf.evaluate({"transitions": []}, {},
                      {"skips": [{"id": "t_a"}, "t_b",
                                 {"id": "t_ok", "reason": "origin-locked"}]},
                      impl_url="http://x")
    assert sorted(art["unreasonedSkipIds"]) == ["t_a", "t_b"]


def test_summary_line_states_that_firing_is_not_trajectory_fidelity() -> None:
    """"16/16 transitions fire" reads as a fidelity result but is not one.

    The gate proves a measured runtime delta — that something moved. A stats bar
    that snaps to full height before its section is on screen, and a nav whose
    seven labels all sit half-expanded, both "fire". A summary that stops at the
    count invites exactly the reading that certified a visibly wrong clone, so
    the line has to name what it did not measure and where that lives.
    """
    from ui_clone.gates.transition_fires import summary_line

    line = summary_line({"fired": 16, "total": 16, "known_skip": 0, "failed": 0})

    # existing consumers match on the "N/M transitions fire" prefix
    assert line.startswith("16/16 transitions fire")
    lowered = line.lower()
    assert "fidelity" in lowered, line
    assert "scroll-coverage" in lowered or "video-motion" in lowered, line
