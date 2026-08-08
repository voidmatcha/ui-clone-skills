"""Per-element scrub mechanics in ui_clone.gates.transition_fires.

Loop-9 regression class: resources-deck-reshuffle (dynamic:false scroll
scrub, spec declares per-card y/x/rotate/z-index evolution at progress
fractions) was COMPLETELY STATIC in the impl yet passed — binary
`varied=True` from any single moving signal (the sticky container itself)
satisfied the old scrub verdict. For entries declaring per-element params,
binary variation is no longer sufficient: per-child series must change
across the sampled fractions AND the cards must move RELATIVE to each
other (a fan translating as one block is still a dead reshuffle).
"""

from __future__ import annotations

import ui_clone.gates.transition_fires as tf

DECK_ENTRY = {
    "id": "resources-deck-reshuffle",
    "trigger": "scroll scrub (sticky section progress)",
    "target": ".resources_sticky",
    "animation": {
        "property": "transform: y, x, rotate; z-index",
        "from": "stacked deck",
        "to": "reshuffled positions at 50%/75% progress",
        "duration": "scroll-scrubbed",
    },
    "dynamic": False,
}

PLAIN_ENTRY = {
    "id": "hero-width-scrub",
    "trigger": "scroll scrub",
    "target": ".hero",
    "animation": {"property": "width", "duration": "scroll-scrubbed"},
}


def _sig(cards: list[tuple[float, float, float, int]]) -> str:
    """childSig in driver format: transform|opacity|zIndex; per child."""
    parts = []
    for tx, ty, rot_a, z in cards:
        parts.append(f"matrix({1 - abs(rot_a)}, {rot_a}, {-rot_a}, {1 - abs(rot_a)}, {tx}, {ty})|1|{z}")
    return ";".join(parts) + ";"


def _samples(per_sample_cards: list[list[tuple[float, float, float, int]]],
             *, target_transforms: list[str] | None = None) -> list[dict]:
    out = []
    for i, cards in enumerate(per_sample_cards):
        out.append({
            "transform": (target_transforms or ["none"] * len(per_sample_cards))[i],
            "opacity": 1.0,
            "top": 200.0 - i * 10,
            "width": 800.0,
            "childSig": _sig(cards),
            "childColorSig": "",
            "scrollY": 1000.0 + i * 400,
            "docH": 20000.0,
            "smoothEngine": False,
        })
    return out


STATIC_FAN = [(0.0, 0.0, 0.0, 1), (40.0, 10.0, 0.12, 2), (80.0, 20.0, -0.12, 3)]


def test_static_deck_with_varied_container_fails() -> None:
    """The loop-9 hole: the sticky container's own transform varies (so the
    old binary check passed) while every card holds the same static fan."""
    samples = _samples(
        [STATIC_FAN] * 5,
        target_transforms=[f"matrix(1, 0, 0, 1, 0, {-i * 30})" for i in range(5)],
    )
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": samples}, set())
    assert d["status"] == "fail", d
    assert "per-element" in str(d.get("observed", "")), d


def test_uniform_block_translation_fails_relative_differentiation() -> None:
    """All cards translate by the same delta per fraction — they change, but
    never move relative to each other: still a dead reshuffle."""
    per_sample = []
    for i in range(5):
        shift = i * 25.0
        # every declared channel moves, but identically on every card —
        # the fan never reshuffles
        per_sample.append([
            (tx + shift, ty + shift, rot + i * 0.02, z + i)
            for tx, ty, rot, z in STATIC_FAN
        ])
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": _samples(per_sample)}, set())
    assert d["status"] == "fail", d
    assert "relative" in str(d.get("observed", "")), d


def test_healthy_reshuffle_passes() -> None:
    per_sample = []
    for i in range(5):
        cards = [
            (0.0 + i * 5, 0.0, 0.0, 1 + (i % 3)),
            (40.0 - i * 12, 10.0 + i * 8, 0.12 - i * 0.05, 2),
            (80.0 + i * 3, 20.0 - i * 6, -0.12 + i * 0.04, 3 - (i % 2)),
        ]
        per_sample.append(cards)
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": _samples(per_sample)}, set())
    assert d["status"] == "pass", d


def test_plain_scrub_keeps_binary_behavior() -> None:
    """Entries without per-element params keep the existing varied semantics."""
    samples = _samples(
        [STATIC_FAN] * 5,
        target_transforms=[f"matrix(1, 0, 0, 1, 0, {-i * 30})" for i in range(5)],
    )
    d = tf.decide(PLAIN_ENTRY, {"found": True, "samples": samples}, set())
    assert d["status"] == "pass", d


def test_smooth_engine_block_stays_unmeasurable() -> None:
    """Lenis-style unscrollable pages remain honest-unmeasurable for
    per-element entries too — couldn't drive is not dead."""
    samples = _samples([STATIC_FAN] * 5)
    for s in samples:
        s["scrollY"] = 1000.0  # frozen
        s["smoothEngine"] = True
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": samples}, set())
    assert d["status"] == "unmeasurable", d


def test_missing_child_signatures_fail_explicitly() -> None:
    """Old payloads without childSig cannot prove per-element motion — that
    is a fail with a named gap, not a fallback to the binary check."""
    samples = _samples(
        [STATIC_FAN] * 5,
        target_transforms=[f"matrix(1, 0, 0, 1, 0, {-i * 30})" for i in range(5)],
    )
    for s in samples:
        s["childSig"] = ""
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": samples}, set())
    assert d["status"] == "fail", d
    assert "per-element" in str(d.get("observed", "")), d


def test_legacy_two_field_sig_still_measurable() -> None:
    """Pre-zIndex childSig (transform|opacity;) still drives the transform
    channels — only the z channel is absent (entry here declares x/y only)."""
    entry = dict(DECK_ENTRY)
    entry["animation"] = {
        "property": "transform: y, x", "duration": "scroll-scrubbed",
    }
    per_sample = []
    for i in range(5):
        cards = [
            f"matrix(1, 0, 0, 1, {i * 5}, 0)|1",
            f"matrix(1, 0, 0, 1, {40 - i * 12}, {10 + i * 8})|1",
        ]
        per_sample.append(";".join(cards) + ";")
    samples = _samples([STATIC_FAN] * 5)
    for s, sig in zip(samples, per_sample):
        s["childSig"] = sig
    d = tf.decide(entry, {"found": True, "samples": samples}, set())
    assert d["status"] == "pass", d


def test_rotation_only_motion_fails_declared_channel_coverage() -> None:
    """The ACTUAL loop-9 deck signature (measured live): cards pick up small
    rotations across the sticky range while translation and z-order — both
    spec-declared — never change. Declared channels must each show motion."""
    per_sample = []
    for i in range(5):
        cards = [
            (0.0, 600.0, 0.0 - i * 0.03, 0),
            (0.0, 700.0, 0.02 * i, 1),
            (0.0, 800.0, -0.01 * i, 2),
            (0.0, 900.0, 0.0, 3),
        ]
        per_sample.append(cards)
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": _samples(per_sample)}, set())
    assert d["status"] == "fail", d
    obs = str(d.get("observed", ""))
    assert "channel" in obs and ("z-index" in obs or "translate" in obs), d


def test_declared_z_with_legacy_sig_is_missing_evidence() -> None:
    """An entry declaring z-index judged against a legacy sig that carries no
    z field cannot pretend the channel moved — explicit fail, not a pass."""
    per_sample = []
    for i in range(5):
        cards = [
            f"matrix(1, 0, 0, 1, {i * 5}, {600 + i * 7})|1",
            f"matrix(1, 0, 0, 1, {40 - i * 12}, {700 + i * 8})|1",
        ]
        per_sample.append(";".join(cards) + ";")
    samples = _samples([STATIC_FAN] * 5)
    for s, sig in zip(samples, per_sample):
        s["childSig"] = sig
    d = tf.decide(DECK_ENTRY, {"found": True, "samples": samples}, set())
    assert d["status"] == "fail", d
    assert "z-index" in str(d.get("observed", "")), d
