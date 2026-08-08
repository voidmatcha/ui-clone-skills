"""Fix M (loop-e2e-6): reveal entries whose declared animation property is bar
height growth / numeric count-up fire with NO opacity/transform/stroke delta —
the stats section (browser-verified on the reference: children stay
transform:none/opacity:1 throughout; bars grow via inline height 0->Npx and
percentages count 0->50/75/90 as textContent). The reveal judgment needs a
height/text channel, gated on the spec-declared property the same way
strokeDashoffset already is (e.prop precedent)."""

from __future__ import annotations

from ui_clone.gates.transition_fires import (
    _child_height_grew,
    _text_digest_changed,
    decide,
)

STATS_ENTRY = {
    "id": "stats-count-up-bar-grow",
    "trigger": "IO reveal (useInView margin 100px) — viewport entry",
    "target": ".dga_stats__Wj1Kx",
    "animation": {
        "property": "textContent count-up; bar height",
        "from": "0 / height 0",
        "to": "final stat value / full bar height",
        "duration": "2s",
    },
}

OPACITY_ENTRY = {
    "id": "plain-fade",
    "trigger": "IO reveal — viewport entry",
    "target": ".x",
    "animation": {"property": "opacity", "from": 0, "to": 1},
}


def test_child_height_grew_detects_bar_growth_from_zero() -> None:
    assert _child_height_grew(
        {"childHeights": [0, 0, 0]}, {"childHeights": [181, 271, 325]}
    )
    assert _child_height_grew(
        {"childHeights": [40, 0.5]}, {"childHeights": [40, 92]}
    )


def test_child_height_grew_rejects_nonzero_before_and_static() -> None:
    # anti-loosening: growth must start from ~0 (a bar at rest), not from a
    # partially-rendered box (lazy image/font reflow on scrollIntoView).
    assert not _child_height_grew(
        {"childHeights": [120, 200]}, {"childHeights": [181, 271]}
    )
    assert not _child_height_grew(
        {"childHeights": [0, 0]}, {"childHeights": [0, 0]}
    )
    assert not _child_height_grew({}, {"childHeights": [181]})
    assert not _child_height_grew({"childHeights": [0]}, {})
    # sub-threshold growth (scrollbar/rounding jitter) does not count
    assert not _child_height_grew({"childHeights": [0]}, {"childHeights": [6]})


def test_text_digest_changed_detects_count_up() -> None:
    assert _text_digest_changed(
        {"textDigest": "050075090"}, {"textDigest": "505075759090"}
    )
    assert not _text_digest_changed(
        {"textDigest": "505075759090"}, {"textDigest": "505075759090"}
    )
    assert not _text_digest_changed({}, {"textDigest": "505075759090"})
    assert not _text_digest_changed({"textDigest": "1"}, {})


def test_reveal_passes_on_height_growth_with_declared_height_property() -> None:
    obs = {
        "found": True,
        "before": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [0, 0, 0],
            "textDigest": "050075090",
        },
        "after": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [181, 271, 325],
            "textDigest": "050075090",
        },
    }
    res = decide(STATS_ENTRY, obs, set())
    assert res["status"] == "pass", res


def test_reveal_passes_on_text_digest_with_declared_count_property() -> None:
    obs = {
        "found": True,
        "before": {"opacity": "1", "transform": "none", "textDigest": "050075090"},
        "after": {"opacity": "1", "transform": "none", "textDigest": "505075759090"},
    }
    res = decide(STATS_ENTRY, obs, set())
    assert res["status"] == "pass", res


def test_reveal_height_text_channels_require_declared_property() -> None:
    # anti-bypass: an entry that declares opacity must NOT pass via a child
    # height growth or text change (live clocks, lazy media) — the channel is
    # selected by the spec-declared property, mirroring strokeDashoffset.
    obs = {
        "found": True,
        "before": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [0],
            "textDigest": "0",
        },
        "after": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [200],
            "textDigest": "50",
        },
    }
    res = decide(OPACITY_ENTRY, obs, set())
    assert res["status"] == "fail", res


def test_reveal_flat_height_and_text_still_fails() -> None:
    obs = {
        "found": True,
        "before": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [181, 271, 325],
            "textDigest": "505075759090",
        },
        "after": {
            "opacity": "1",
            "transform": "none",
            "childHeights": [181, 271, 325],
            "textDigest": "505075759090",
        },
    }
    res = decide(STATS_ENTRY, obs, set())
    assert res["status"] == "fail", res


# ── Fix K + L (codex-required anti-bypass cases) ──────────────────────────


def test_dead_scrub_with_extended_early_samples_still_fails() -> None:
    """Fix K: extra early sweep positions (clamped to scrollY=0, possibly
    duplicated) must not create a PASS for a genuinely dead scrub — variation
    still requires the element's own measured properties to change across
    scroll-advanced samples."""
    from ui_clone.gates.transition_fires import _samples_vary

    flat = {
        "transform": "none",
        "opacity": 1,
        "childSig": "none|1;none|1;",
        "childColorSig": "rgb(1,2,3);rgb(1,2,3);",
        "width": 1024,
    }
    samples = [
        {**flat, "scrollY": 0},
        {**flat, "scrollY": 0},  # clamped duplicate of the early position
        {**flat, "scrollY": 300},
        {**flat, "scrollY": 700},
    ]
    assert not _samples_vary(samples, "width")


def test_scrub_color_series_counts_only_with_declared_color_property() -> None:
    """Fix L: childColorSig variation across advanced scroll passes ONLY when
    the spec declares a color-family property — scroll-correlated color from
    animation-timeline:scroll() or a body.scrolled class must not pass a
    dead transform/opacity scrub (codex review)."""
    from ui_clone.gates.transition_fires import _samples_vary

    base = {"transform": "none", "opacity": 1, "childSig": "none|1;"}
    samples = [
        {**base, "scrollY": 0, "childColorSig": "rgb(141,125,125);"},
        {**base, "scrollY": 600, "childColorSig": "rgb(253,251,238);"},
    ]
    # declared color property (word-reveal class swap) -> varied
    assert _samples_vary(samples, "per-word class swap, opacity/color transition")
    # dead transform scrub with unrelated scroll-linked color -> NOT varied
    assert not _samples_vary(samples, "transform")
    assert not _samples_vary(samples, "opacity")
    # color variation at frozen scroll never counts even when declared
    frozen = [
        {**base, "scrollY": 0, "childColorSig": "rgb(141,125,125);"},
        {**base, "scrollY": 0, "childColorSig": "rgb(253,251,238);"},
    ]
    assert not _samples_vary(frozen, "color")
