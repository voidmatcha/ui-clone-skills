from __future__ import annotations

from ui_clone.gates.transition_fires import _stroke_drew, decide

REVEAL_ENTRY = {
    "id": "svg-stroke-draw",
    "trigger": "in-view / scroll state (t ? drawn : hidden)",
    "target": "decorative SVG strokes",
    "animation": {"property": "strokeDashoffset", "from": "dashLength", "to": 0},
}


def test_stroke_drew_detects_draw_in() -> None:
    """A stroke draw-in animates strokeDashoffset length->0 and touches neither
    opacity nor transform — the reveal judgment false-negatived a firing draw
    ('opacity 0 -> 0, transform I -> I'). A decreasing offset magnitude is the
    measured delta."""
    assert _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "0px"})
    assert _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "12px"})


def test_stroke_drew_rejects_static_and_draw_out() -> None:
    # anti-loosening: static dashes and draw-OUTs are not the spec'd reveal
    assert not _stroke_drew({"strokeDashoffset": "240px"}, {"strokeDashoffset": "240px"})
    assert not _stroke_drew({"strokeDashoffset": "0px"}, {"strokeDashoffset": "240px"})
    assert not _stroke_drew({}, {"strokeDashoffset": "0px"})
    assert not _stroke_drew({"strokeDashoffset": "240px"}, {})
    assert not _stroke_drew({"strokeDashoffset": "abc"}, {"strokeDashoffset": "0px"})


def test_reveal_decision_passes_on_stroke_delta_alone() -> None:
    obs = {
        "found": True,
        "before": {"opacity": "1", "transform": "none", "strokeDashoffset": "240px"},
        "after": {"opacity": "1", "transform": "none", "strokeDashoffset": "0px"},
    }
    res = decide(REVEAL_ENTRY, obs, set())
    assert res["status"] == "pass", res
    assert "strokeDashoffset" in res["observed"], res


def test_reveal_decision_still_fails_without_any_delta() -> None:
    obs = {
        "found": True,
        "before": {"opacity": "0", "transform": "none", "strokeDashoffset": "240px"},
        "after": {"opacity": "0", "transform": "none", "strokeDashoffset": "240px"},
    }
    res = decide(REVEAL_ENTRY, obs, set())
    assert res["status"] == "fail", res


def test_scrub_samples_vary_via_child_signature() -> None:
    """loop-e2e-5: 7 scrubs read flat because motion lives in DESCENDANTS
    (deck cards, word-reveal spans) while samples recorded only the target's
    own transform/opacity. childSig variation across scroll samples is the
    same anti-loosening signal class (children's transform|opacity only —
    no scroll-coupled positions)."""
    from ui_clone.gates.transition_fires import _samples_vary

    flat_el = {"transform": "none", "opacity": 1}
    samples = [
        dict(flat_el, scrollY=11600, childSig="none|1;matrix(1, 0, 0, 1, 0, 600)|1;"),
        dict(flat_el, scrollY=12400, childSig="none|1;matrix(1, 0, 0, 1, 0, 300)|1;"),
        dict(flat_el, scrollY=13200, childSig="none|1;matrix(1, 0, 0, 1, 0, -140)|1;"),
    ]
    assert _samples_vary(samples)


def test_scrub_childsig_without_scroll_advancement_does_not_count() -> None:
    """Anti-bypass (codex review): a load-time child opacity flip while the
    page never advanced is not scrub evidence — childSig/width variation
    counts only when the scroll position actually moved across the samples."""
    from ui_clone.gates.transition_fires import _samples_vary

    flat_el = {"transform": "none", "opacity": 1}
    samples = [
        dict(flat_el, scrollY=0, childSig="none|0;"),
        dict(flat_el, scrollY=0, childSig="none|1;"),
        dict(flat_el, scrollY=0, childSig="none|1;"),
    ]
    assert not _samples_vary(samples)


def test_scrub_samples_vary_via_inline_width() -> None:
    """hero-video width scrub: ref drives inline width 80vw->100vw — no
    transform at all. Width series variation must count."""
    from ui_clone.gates.transition_fires import _samples_vary

    flat = {"transform": "none", "opacity": 1}
    samples = [
        dict(flat, scrollY=0, width=1152.0),
        dict(flat, scrollY=300, width=1290.0),
        dict(flat, scrollY=600, width=1440.0),
    ]
    assert _samples_vary(samples)


def test_scrub_flat_childsig_and_width_still_dead() -> None:
    from ui_clone.gates.transition_fires import _samples_vary

    flat = {"transform": "none", "opacity": 1, "childSig": "none|1;", "width": 1152.0}
    assert not _samples_vary([dict(flat), dict(flat), dict(flat)])


def test_scrub_running_css_animation_transform_does_not_count() -> None:
    """verify-H2: a dead scrub target with a looping CSS animation (marquee /
    float / pulse) varies its OWN transform/opacity on a timer, not in response
    to scroll. With childSig/width flat, that timer motion must not be counted
    as scrub evidence (same noise scroll-end-completion skips at collection)."""
    from ui_clone.gates.transition_fires import _samples_vary

    samples = [
        {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1, "scrollY": 100,
         "childSig": "none|1;", "width": 1000.0, "animRunning": True},
        {"transform": "matrix(1, 0, 0, 1, 40, 0)", "opacity": 1, "scrollY": 400,
         "childSig": "none|1;", "width": 1000.0, "animRunning": True},
        {"transform": "matrix(1, 0, 0, 1, -30, 0)", "opacity": 1, "scrollY": 700,
         "childSig": "none|1;", "width": 1000.0, "animRunning": True},
    ]
    assert not _samples_vary(samples)


def test_scrub_transform_without_running_animation_still_counts() -> None:
    """No regression: a genuine scrub (no running CSS animation) whose target
    transform moves with scroll must still count."""
    from ui_clone.gates.transition_fires import _samples_vary

    samples = [
        {"transform": "matrix(1, 0, 0, 1, 0, 0)", "opacity": 1, "scrollY": 100},
        {"transform": "matrix(1, 0, 0, 1, 40, 0)", "opacity": 1, "scrollY": 400},
    ]
    assert _samples_vary(samples)


def _decide(entry: dict, obs: dict) -> dict:
    from ui_clone.gates.transition_fires import decide

    return decide(entry, obs, set())


def test_reveal_flat_after_state_accepts_load_phase_motion() -> None:
    """loop-e2e-5: intro overlay settles at 2.45s and IO reveals fire during
    the mount sweep — by PHASE2 the after-state reads flat (opacity 0->0).
    A load-phase series captured right after a fresh navigate carries the
    actual firing and must count as the runtime evidence."""
    entry = {"id": "intro-overlay-reveal", "target": ".intro", "trigger": "page load reveal"}
    obs = {
        "found": True,
        "before": {"opacity": 0, "transform": "none"},
        "after": {"opacity": 0, "transform": "none"},
        "samples": [],
        "loadSamples": [
            {"opacity": 1, "transform": "none", "childSig": "none|1;"},
            {"opacity": 1, "transform": "matrix(1, 0, 0, 1, 0, -300)", "childSig": "none|1;"},
            {"opacity": 0, "transform": "matrix(1, 0, 0, 1, 0, -900)", "childSig": "none|1;"},
        ],
    }
    res = _decide(entry, obs)
    assert res["status"] == "pass", res
    assert "load-phase" in res["observed"], res


def test_reveal_flat_everywhere_still_fails() -> None:
    entry = {"id": "stats-count-up", "target": ".stats", "trigger": "IO reveal"}
    flat = {"opacity": 1, "transform": "none", "childSig": "none|1;"}
    obs = {
        "found": True,
        "before": dict(flat),
        "after": dict(flat),
        "samples": [],
        "loadSamples": [dict(flat), dict(flat), dict(flat)],
    }
    res = _decide(entry, obs)
    assert res["status"] == "fail", res


def test_carousel_accepts_load_phase_img_rotation() -> None:
    """Timer carousel rotates CONTENT (img srcs), not transforms — two
    load-phase samples ~3.6s apart with a different img-src set prove the
    interval runtime."""
    entry = {
        "id": "eatreal-food-carousel",
        "target": ".dga_cards",
        "trigger": "timer (setInterval 3500ms)",
        "animation": {"type": "carousel"},
    }
    obs = {
        "found": True,
        "before": {"opacity": 1, "transform": "none"},
        "after": {"opacity": 1, "transform": "none"},
        "samples": [],
        "loadSamples": [
            {"opacity": 1, "transform": "none", "imgSrcs": ["salmon.webp", "cheese.webp"]},
            {"opacity": 1, "transform": "none", "imgSrcs": ["butter.webp", "salmon.webp"]},
        ],
    }
    res = _decide(entry, obs)
    assert res["status"] == "pass", res


def test_io_reveal_cannot_pass_via_load_phase() -> None:
    """Anti-bypass (codex review): a clone that fades content in at
    DOMContentLoaded — no IO callback, no scroll trigger — must NOT pass an
    IO/scroll reveal entry through the load-phase path. Load-phase evidence
    is reserved for page-load/splash/autoplay/timer triggers; IO reveals get
    their evidence from the pre-sweep initial snapshot instead."""
    entry = {"id": "stats-count-up-bar-grow", "target": ".stats", "trigger": "IO reveal (useInView margin 100px)"}
    flat = {"opacity": 1, "transform": "none"}
    obs = {
        "found": True,
        "before": dict(flat),
        "after": dict(flat),
        "samples": [],
        "loadSamples": [
            {"opacity": 0, "transform": "none"},
            {"opacity": 1, "transform": "none"},
        ],
    }
    res = _decide(entry, obs)
    assert res["status"] == "fail", res
