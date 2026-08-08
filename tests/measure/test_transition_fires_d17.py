"""D17 (loop-nvti-0): transition-fires under-measured a live GSAP page 2/7
vs 6/7 live-verified. Three measurement blind spots, each with a spec-shaped
regression here:

(a) near-top scrub sweeps clamp every scroll target to ~0; `_scroll_blocked`
    then reported "smooth-scroll intercept" on a page with NO smooth engine
    (hero-parallax-item-scroll-scrub). When the driver AFFIRMATIVELY recorded
    smoothEngine=false, a frozen scrollY is the sampler's own clamp — never
    "unmeasurable". Legacy payloads without the key keep the old inference.
(b) scroll state machines (`trigger: "scroll state machine …"`) classified as
    "reveal" and probed with a single scrollIntoView snap — the y-dependent
    paragraph swap is invisible at one position (hero-paragraph-state-machine).
    They are scroll-position-driven: classify as scrub (multi-position sweep).
(c) count-up declarations live in the spec's id/trigger/property blob
    ("counter-digit-roll", "digit columns translate"), but the textDigest
    channel only consulted `animation.property` for the literal "count" —
    the counter fell through to opacity/transform and failed
    (counter-digit-roll: text 1 -> 115, styles flat).
"""

from __future__ import annotations

from pathlib import Path

from ui_clone.gates import transition_fires as tf

SCRIPT = (Path(__file__).resolve().parents[2]
          / "skills" / "visual-debug" / "scripts" / "transition-fires-check.sh")


# ── (a) _scroll_blocked: affirmed engine absence is never "blocked" ──────
def test_scroll_blocked_false_when_engine_affirmatively_absent() -> None:
    samples = [
        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
         "smoothEngine": False},
        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
         "smoothEngine": False},
    ]
    assert tf._scroll_blocked(samples) is False


def test_scroll_blocked_legacy_payload_without_engine_key_stays_blocked() -> None:
    # Back-compat: old drivers never recorded smoothEngine; keep the frozen-
    # scroll inference for them (test_scroll_scrub_unmeasurable_when_scroll_blocked).
    samples = [
        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000},
        {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000},
    ]
    assert tf._scroll_blocked(samples) is True


def test_scrub_flat_with_affirmed_no_engine_is_fail_not_unmeasurable() -> None:
    entry = {"id": "hero-parallax-item-scroll-scrub",
             "trigger": "scroll-scrub (.item-outer y follows tech-hero scroll progress)",
             "animation": {"property": "transform:translateY", "scrub": "scroll-linked"},
             "target": ".item-outer"}
    obs = {"found": True, "before": {}, "after": {},
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
                "smoothEngine": False},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
                "smoothEngine": False},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
                "smoothEngine": False},
           ]}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


def test_scrub_wrapper_mode_with_engine_stays_unmeasurable() -> None:
    entry = {"id": "t_scrub", "trigger": "scroll-scrub",
             "animation": {"type": "scrub"}, "target": ".x"}
    obs = {"found": True, "before": {}, "after": {},
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000,
                "smoothEngine": True},
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 4000,
                "smoothEngine": True},
           ]}
    assert tf.decide(entry, obs, set())["status"] == "unmeasurable"


# ── (b) scroll state machines are scroll-position-driven: scrub kind ─────
def test_classify_scroll_state_machine_as_scrub() -> None:
    entry = {
        "id": "hero-paragraph-state-machine",
        "trigger": "scroll state machine (scroll position within .tech-hero "
                   "selects active paragraph state)",
        "target": ".tech-hero .paragraph-1",
        "animation": {"property": "opacity, transform:translateY",
                      "from": "opacity:0, y:100%", "to": "opacity:1, y:0%"},
    }
    assert tf.classify(entry) == "scrub"


def test_classify_click_state_machine_not_scrub() -> None:
    # Guard: only SCROLL-driven state machines become scrubs; a click-driven
    # state machine keeps its click measurement.
    entry = {
        "id": "tabs-state-machine",
        "trigger": "click state machine (tab selects active panel state)",
        "target": ".tabs",
        "animation": {"property": "opacity"},
    }
    assert tf.classify(entry) == "click"


def test_state_machine_scrub_passes_on_child_opacity_swap_across_scroll() -> None:
    # The paragraph swap lives in child opacity (p1 1->0, p2 0->1) while the
    # container transform stays identity — the scrub sweep's childSig channel
    # must catch it once the entry is classified scrub.
    entry = {
        "id": "hero-paragraph-state-machine",
        "trigger": "scroll state machine (scroll position selects paragraph)",
        "target": ".tech-hero .paragraphs",
        "animation": {"property": "opacity, transform:translateY"},
    }
    obs = {"found": True, "before": {}, "after": {},
           "samples": [
               {"transform": "none", "opacity": 1.0, "scrollY": 0, "docH": 23000,
                "childSig": "none|1;none|0;", "smoothEngine": False},
               {"transform": "none", "opacity": 1.0, "scrollY": 1200, "docH": 23000,
                "childSig": "none|0;none|1;", "smoothEngine": False},
           ]}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "pass", d


# ── (c) count-up declaration mined from the whole spec entry blob ────────
def _counter_entry() -> dict:
    return {
        "id": "counter-digit-roll",
        "trigger": "scroll enter (ScrollTrigger on div.counter; digit columns "
                   "translate to final value)",
        "target": ".js-nav-section .counter",
        "animation": {"property": "transform:translateY per digit column",
                      "from": "y:0 (digit 0)", "to": "y:-(digit height * value)px"},
    }


def test_counter_digit_roll_passes_on_text_digest() -> None:
    obs = {"found": True,
           "before": {"opacity": 1.0, "transform": "none", "height": 40.0,
                      "textDigest": "1"},
           "after": {"opacity": 1.0, "transform": "none", "height": 40.0,
                     "textDigest": "115"}}
    d = tf.decide(_counter_entry(), obs, set())
    assert d["status"] == "pass", d


def test_counter_digit_roll_flat_digest_still_fails() -> None:
    obs = {"found": True,
           "before": {"opacity": 1.0, "transform": "none", "height": 40.0,
                      "textDigest": "1"},
           "after": {"opacity": 1.0, "transform": "none", "height": 40.0,
                     "textDigest": "1"}}
    d = tf.decide(_counter_entry(), obs, set())
    assert d["status"] == "fail", d


def test_undeclared_text_change_still_not_motion_evidence() -> None:
    # Anti-bypass guard stays: an entry whose spec blob declares NO count/digit
    # channel must not pass on a text delta (dynamic content is not motion).
    entry = {
        "id": "promo-reveal",
        "trigger": "IO reveal — viewport entry",
        "target": ".promo",
        "animation": {"property": "opacity, transform:translateY"},
    }
    obs = {"found": True,
           "before": {"opacity": 1.0, "transform": "none", "height": 40.0,
                      "textDigest": "20260713"},
           "after": {"opacity": 1.0, "transform": "none", "height": 40.0,
                     "textDigest": "20260714"}}
    d = tf.decide(entry, obs, set())
    assert d["status"] == "fail", d


# ── driver seam: global sweep fallback exists in the sampler JS ──────────
def test_driver_scrub_sampler_has_global_sweep_fallback() -> None:
    """The clamp fix lives in an embedded-JS heredoc (not importable) — a
    source-level guard on that seam, same precedent as
    test_obs_merge_forwards_carousel_fingerprint."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "globalSweep" in src, (
        "scrub sampler must fall back to absolute docH-fraction samples when "
        "the local sweep's scroll coverage is clamped (D17a near-top scrubs)"
    )


def test_default_chunk_size_is_one() -> None:
    """H4 (loop-nvti-2/4): TF_CHUNK_SIZE=5 exhausted the eval budget on
    scrub-heavy chunks — the same impl measured 2/7..6/7 varying only chunk
    size. The default must keep one entry per chunk."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'CHUNK_SIZE="${TF_CHUNK_SIZE:-1}"' in src
