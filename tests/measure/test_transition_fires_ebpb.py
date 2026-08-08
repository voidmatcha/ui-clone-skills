"""L-MEA-2/3/8 (loop-ebpb-0): three transition-fires probe-SHAPE defects, each
with a manual live counter-proof — the transition fires but the probe cannot
see it. RED->GREEN judge tests plus source-level seam guards on the embedded-JS
driver (mirrors the D17 precedent: the browser-driving heredoc is not
importable, so its seams are guarded by asserting on the script source).

L-MEA-2 class-toggle probed as scrub: a scroll-driven className toggle (header
    gains a shadow class past scrollY>0, animating box-shadow) is invisible to
    the scrub sampler's transform/opacity/childSig channels. The sampler now
    records the element's className per sample; the judge counts a className
    CHANGE only when the spec-side decl blob declares a class/state/toggle, and
    only across advanced scroll (anti-loosening: undeclared className churn must
    not count).
L-MEA-3 canvas probed offscreen: an IO play/paused canvas is legitimately
    static offscreen, so the webgl probe read it dead without scrolling it into
    view. The driver now scrollIntoView()s the canvas before the after-snapshot.
L-MEA-8 one-shot IO reveal sampled post-settle: a reveal that completes during
    the mount sweep reads before==after==final. The driver re-probes from a
    fresh navigate (target out of view -> scrolled in, sampled); the judge
    accepts the pre->settled delta WITHOUT reopening the loadSamples IO
    exclusion.
"""

from __future__ import annotations

import re
from pathlib import Path

from ui_clone.gates import transition_fires as tf

SCRIPT = (Path(__file__).resolve().parents[2]
          / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh")


def _state(**kw: object) -> dict:
    base: dict[str, object] = {"opacity": None, "transform": None}
    base.update(kw)
    return base


# ── L-MEA-2: class-toggle scrub — declared className change counts ────────
def _header_shadow_entry() -> dict:
    # Verbatim shape of tmp/ref/ebpb header-shadow-state: classified scrub
    # (scroll state machine), animates box-shadow via a class toggle. The
    # toggled class is NAMED in description/bundle_branch (as in the real spec),
    # which the C3 token-membership check requires.
    return {
        "id": "header-shadow-state",
        "trigger": "scroll state machine (scrollY==0 <-> scrollY>0 class toggle)",
        "description": "Fixed header gains style_header__shadow__9G5rH class "
                       "when scrollY>0, removed at scrollY=0.",
        "bundle_branch": "scrollY>0 => add style_header__shadow__9G5rH; "
                         "scrollY==0 => remove",
        "animation": {"property": "box-shadow (class-gated)"},
        "target": ".style_header__tjhHk",
    }


def test_class_toggle_scrub_passes_on_declared_classname_change() -> None:
    # C3: the changed token style_header__shadow__9G5rH IS named in the spec
    # (description/bundle_branch) -> counts.
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0,
                "cls": "style_header__tjhHk"},
               {"transform": "none", "opacity": 1.0, "scrollY": 400,
                "cls": "style_header__tjhHk style_header__shadow__9G5rH"},
           ]}
    d = tf.decide(_header_shadow_entry(), obs, set())
    assert d["status"] == "pass", d


def test_class_toggle_scrub_hash_regenerated_still_passes() -> None:
    # C3 robustness: a regenerated impl emits a DIFFERENT module hash
    # (style_header__shadow__QqZ21) for the same class — the hash suffix is
    # stripped so the stable base still matches the spec-named token.
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0,
                "cls": "style_header__tjhHk"},
               {"transform": "none", "opacity": 1.0, "scrollY": 400,
                "cls": "style_header__tjhHk style_header__shadow__QqZ21"},
           ]}
    d = tf.decide(_header_shadow_entry(), obs, set())
    assert d["status"] == "pass", d


def test_class_toggle_timer_churn_token_not_named_fails() -> None:
    # C3 (false-pass guard): a scrub whose decl blob carries the keyword "state"
    # via an ordinary id (hero-paragraph-state-machine) targeting an element
    # whose class mutates on a TIMER (carousel active-slide 'is-active'). The
    # 2 scroll samples span wall time, so the class changed — but the changed
    # token is NOT named in the spec, so it must not count. Dead scrub => fail.
    entry = {"id": "hero-paragraph-state-machine", "trigger": "scroll-scrub",
             "animation": {"property": "transform:translateY"},
             "target": ".paragraphs"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "cls": "slide"},
               {"transform": "none", "opacity": 1.0, "scrollY": 1200,
                "cls": "slide is-active"},
           ]}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_undeclared_classname_churn_does_not_count() -> None:
    # Anti-loosening: a scrub whose decl blob declares NO class/state/toggle
    # (a carousel-ish track auto-advancing its active-slide className) must NOT
    # pass on className churn alone — transform/opacity stayed flat -> dead.
    entry = {"id": "auto-track-scrub", "trigger": "scroll-scrub",
             "animation": {"property": "transform:translateX"}, "target": ".track"}
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0,
                "cls": "slide is-active"},
               {"transform": "none", "opacity": 1.0, "scrollY": 1200,
                "cls": "slide"},
           ]}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_class_toggle_frozen_scroll_does_not_count() -> None:
    # The className channel requires ADVANCED scroll, same as childSig/width —
    # a className flip while scrollY never moved is not scrub evidence.
    obs = {"found": True, "before": _state(), "after": _state(),
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "cls": "a"},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "cls": "a b"},
           ]}
    d = tf.decide(_header_shadow_entry(), obs, set())
    assert d["status"] == "fail", d


def test_driver_scrub_sampler_records_classname() -> None:
    """Seam: the scrub sampler must record the element className per sample or
    the judge's class-toggle channel has no data to read."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "cls: (el.getAttribute('class') || '')" in src


# ── L-MEA-3: canvas probed offscreen — scrollIntoView before the snapshot ──
def test_driver_webgl_branch_scrolls_into_view_before_snapshot() -> None:
    """Seam: the webgl branch must bring the canvas on-screen (resuming its IO
    play/paused RAF loop) BEFORE it snapshots canvasInfo, else an offscreen
    paused canvas reads dead."""
    src = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"else if \(e\.kind === 'webgl'\) \{(.*?)\} else if", src, re.S)
    assert m, "webgl branch not found in PHASE2 template"
    body = m.group(1)
    assert "scrollIntoView" in body, "webgl branch must scrollIntoView the canvas"
    assert body.index("scrollIntoView") < body.index("snap(el, e)"), (
        "scrollIntoView must precede the canvas after-snapshot"
    )


# ── L-MEA-8: one-shot IO reveal — fresh-context re-probe ──────────────────
def _cardstack_entry() -> dict:
    return {
        "id": "cardstack-inview-reveal",
        "trigger": "viewport in-view reveal (data-in-view attribute flip on "
                   "scroll into view)",
        "animation": {"property": "transform/opacity reveal"},
        "target": ".home-card-stack .style_card__axFC1",
    }


def _not_active_card_entry() -> dict:
    return {
        "id": "not-active-card-reveal",
        "trigger": "intersection observer reveal toggles active",
        "animation": {"property": "opacity/transform inactive->active"},
        "target": ".card:not(.active)",
    }


def _identical_final_obs(**extra: object) -> dict:
    # Main pass reads before==after==final (the one-shot completed during the
    # settle mount sweep) so every normal reveal channel is flat.
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none"),
           "after": _state(opacity=1.0, transform="none")}
    obs.update(extra)
    return obs


def test_reveal_probe_pre_state_differs_passes() -> None:
    obs = _identical_final_obs(revealProbe={
        "pre": {"opacity": 0.0, "transform": "none"},
        "samples": [
            {"opacity": 0.0, "transform": "none"},
            {"opacity": 0.55, "transform": "none"},
            {"opacity": 1.0, "transform": "none"},
        ],
    })
    d = tf.decide(_cardstack_entry(), obs, set())
    assert d["status"] == "pass", d


def test_not_active_intersection_reveal_baked_active_flat_state_fails() -> None:
    # A :not(.active) selector can retarget after the intended element becomes
    # active. Endpoint differences alone are not faithful motion for this shape:
    # the fresh-context probe must show temporal reveal motion.
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="translateY(24px)"),
           "after": _state(opacity=1.0, transform="none"),
           "revealProbe": {
               "pre": {"opacity": 1.0, "transform": "none"},
               "samples": [
                   {"opacity": 1.0, "transform": "none"},
                   {"opacity": 1.0, "transform": "none"},
                   {"opacity": 1.0, "transform": "none"},
               ],
           }}
    d = tf.decide(_not_active_card_entry(), obs, set())
    assert d["status"] == "fail", d


def test_not_active_intersection_reveal_passes_on_temporal_delta() -> None:
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="translateY(24px)"),
           "after": _state(opacity=1.0, transform="none"),
           "revealProbe": {
               "pre": {"opacity": 0.0, "transform": "translateY(24px)"},
               "samples": [
                   {"opacity": 0.0, "transform": "translateY(24px)",
                    "cls": "card", "transitionDuration": "0s"},
                   {"opacity": 0.45, "transform": "translateY(12px)",
                    "cls": "card active", "transitionDuration": "1.6s"},
                   {"opacity": 1.0, "transform": "none",
                    "cls": "card active", "transitionDuration": "1.6s"},
               ],
           }}
    d = tf.decide(_not_active_card_entry(), obs, set())
    assert d["status"] == "pass", d


def test_not_active_intersection_reveal_zero_duration_jump_fails() -> None:
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="translateY(24px)"),
           "after": _state(opacity=1.0, transform="none"),
           "revealProbe": {
               "pre": {"opacity": 0.0, "transform": "translateY(24px)"},
               "samples": [
                   {"opacity": 1.0, "transform": "none"},
                   {"opacity": 1.0, "transform": "none"},
                   {"opacity": 1.0, "transform": "none"},
               ],
           }}
    d = tf.decide(_not_active_card_entry(), obs, set())
    assert d["status"] == "fail", d


def test_not_active_intersection_reveal_zero_duration_pre_to_terminal_fails() -> None:
    # The first sample can land before the IO callback and the next one after a
    # zero-duration class flip. Endpoint variation is still not animated motion.
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="translateY(24px)"),
           "after": _state(opacity=1.0, transform="none"),
           "revealProbe": {
               "pre": {"opacity": 0.0, "transform": "translateY(24px)"},
               "samples": [
                   {"opacity": 0.0, "transform": "translateY(24px)",
                    "transitionDuration": "0s"},
                   {"opacity": 1.0, "transform": "none",
                    "transitionDuration": "0s"},
                   {"opacity": 1.0, "transform": "none",
                    "transitionDuration": "0s"},
               ],
           }}
    d = tf.decide(_not_active_card_entry(), obs, set())
    assert d["status"] == "fail", d


def test_not_active_intersection_reveal_unrelated_active_churn_fails() -> None:
    obs = {"found": True,
           "before": _state(opacity=0.0, transform="translateY(24px)"),
           "after": _state(opacity=1.0, transform="none"),
           "revealProbe": {
               "pre": {"opacity": 1.0, "transform": "none",
                       "cls": "card carousel-active"},
               "samples": [
                   {"opacity": 1.0, "transform": "none",
                    "cls": "card carousel-active"},
                   {"opacity": 1.0, "transform": "none",
                    "cls": "card"},
                   {"opacity": 1.0, "transform": "none",
                    "cls": "card carousel-active"},
               ],
           }}
    d = tf.decide(_not_active_card_entry(), obs, set())
    assert d["status"] == "fail", d


def test_reveal_probe_inflight_variation_passes_without_pre() -> None:
    # Conditional-mount impl: no pre-state resolvable at top (pre None), but the
    # in-flight samples still capture the opacity rise once scrolled in.
    obs = _identical_final_obs(revealProbe={
        "pre": None,
        "samples": [
            {"opacity": 0.0, "transform": "none"},
            {"opacity": 1.0, "transform": "none"},
        ],
    })
    d = tf.decide(_cardstack_entry(), obs, set())
    assert d["status"] == "pass", d


def test_reveal_probe_flat_still_fails() -> None:
    # Honest fail preserved: even the fresh-context re-probe shows no motion.
    obs = _identical_final_obs(revealProbe={
        "pre": {"opacity": 1.0, "transform": "none"},
        "samples": [
            {"opacity": 1.0, "transform": "none"},
            {"opacity": 1.0, "transform": "none"},
        ],
    })
    d = tf.decide(_cardstack_entry(), obs, set())
    assert d["status"] == "fail", d


def test_reveal_probe_flat_pre_with_inflight_churn_still_fails() -> None:
    # C2 (false-pass guard): a resolvable FLAT pre-state (pre==final, dead
    # reveal) must NOT be overridden by time-driven in-flight churn. An
    # autoplaying carousel / pulsing child inside the card stack varies the
    # container opacity across the 0/300/900ms window while pre->final stays
    # flat — the honest fail must hold (do not fall through to _samples_vary).
    obs = _identical_final_obs(revealProbe={
        "pre": {"opacity": 1.0, "transform": "none"},
        "samples": [
            {"opacity": 1.0, "transform": "none"},
            {"opacity": 0.4, "transform": "none"},
            {"opacity": 1.0, "transform": "none"},
        ],
    })
    d = tf.decide(_cardstack_entry(), obs, set())
    assert d["status"] == "fail", d


def test_reveal_probe_ignored_for_non_io_reveal() -> None:
    # Anti-loosening: the re-probe rescues ONLY IO/inview/intersection reveals.
    # A plain scroll-into-view reveal with a populated revealProbe must not be
    # rescued (the driver would not have built one for it either).
    entry = {"id": "plain-reveal", "trigger": "scroll-into-view",
             "animation": {"type": "fade-up"}, "target": ".x"}
    obs = _identical_final_obs(revealProbe={
        "pre": {"opacity": 0.0, "transform": "none"},
        "samples": [{"opacity": 0.0}, {"opacity": 1.0}],
    })
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_io_reveal_still_cannot_pass_via_load_phase() -> None:
    # The load-phase IO exclusion must stay intact: an IO reveal with ONLY
    # loadSamples (no revealProbe) still fails — the DOMContentLoaded fade
    # bypass is not reopened by the new revealProbe channel.
    entry = {"id": "io-reveal", "trigger": "IO reveal (useInView margin 100px)",
             "animation": {"property": "opacity"}, "target": ".stats"}
    obs = {"found": True,
           "before": _state(opacity=1.0, transform="none"),
           "after": _state(opacity=1.0, transform="none"),
           "loadSamples": [
               {"opacity": 0, "transform": "none"},
               {"opacity": 1, "transform": "none"},
           ]}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_driver_has_fresh_context_reveal_reprobe() -> None:
    """Seam: the driver must build a fresh-navigate reveal re-probe and the
    obs-merge must forward its revealProbe field to the judge."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "REVEAL_TARGETS" in src, "reveal re-probe pass missing"
    assert "reveal re-probe" in src.lower()
    assert '"revealProbe": reveal_series.get(str(i))' in src, (
        "obs-merge must forward the revealProbe field or the channel is dead"
    )
    # C4: the mount hunt + total loop must be bounded so a tall / many-target
    # page cannot silently blow the ~25s eval budget.
    assert "maxHunt" in src, "reveal re-probe mount hunt must be capped (C4)"
    assert "Date.now() - startT" in src, "reveal re-probe needs a wall budget (C4)"
    assert "strict_not_active" in src
    assert "if same or strict_not_active:" in src, (
        ":not(.active) IO rows must always receive a fresh temporal probe, "
        "even when the main endpoint sweep already differs"
    )
