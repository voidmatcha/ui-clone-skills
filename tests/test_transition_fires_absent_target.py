"""transition-fires: a spec target absent from the captured reference page is a
KNOWN-SKIP, not a FAIL.

Auto-mined hover/transition entries (e.g. an IR download button's `:hover` rule
lifted from the site's full CSS) can target elements that live on a SUBPAGE, not
the captured homepage. They cannot fire on ref OR impl, so a downstream "element
not found" is a not-applicable probe — not a clone defect. The reclassification
is gated on the REF capture, so a target that IS on the ref page but missing from
the impl still FAILS as a genuine translation miss.
"""
from __future__ import annotations

from ui_clone.gates.transition_fires import evaluate

# A captured DOM (structure.json shape) that HAS `.present-btn` but NOT
# `.ir-common__button` / `.subpage-only`.
_REF_STRUCTURE = {
    "tag": "body",
    "class": "",
    "children": [
        {"tag": "a", "class": "present-btn quick", "children": []},
        {"tag": "div", "class": "hero", "children": []},
    ],
}

_SPEC = {
    "transitions": [
        {"id": "hover-absent", "trigger": "hover", "target": ".ir-common__button.download"},
        {"id": "hover-present", "trigger": "hover", "target": ".present-btn"},
    ]
}

# Both elements report "not found" on the impl (worst case).
_OBS_NOT_FOUND = {
    "hover-absent": {"found": False, "before": {}, "after": {}},
    "hover-present": {"found": False, "before": {}, "after": {}},
}


def _by_id(artifact: dict) -> dict[str, dict]:
    return {e["id"]: e for e in artifact["entries"]}


def test_absent_target_is_known_skip_present_target_still_fails() -> None:
    art = evaluate(_SPEC, _OBS_NOT_FOUND, {}, ref_structure=_REF_STRUCTURE)
    entries = _by_id(art)
    # absent-from-captured-page target -> known-skip (not a clone defect)
    assert entries["hover-absent"]["status"] == "known-skip", entries["hover-absent"]
    assert "absent from the captured" in entries["hover-absent"]["observed"]
    # target that IS on the ref page but not found on impl -> genuine FAIL
    assert entries["hover-present"]["status"] == "fail", entries["hover-present"]
    assert art["known_skip"] == 1 and art["failed"] == 1


def test_no_ref_structure_preserves_legacy_fail() -> None:
    """Backward compat: without a captured DOM, an unfound target still FAILS
    (the reclassification is disabled, never a silent pass)."""
    art = evaluate(_SPEC, _OBS_NOT_FOUND, {}, ref_structure=None)
    assert art["failed"] == 2, art
    assert art["known_skip"] == 0


def test_runtime_injected_absent_target_is_not_skipped() -> None:
    """A runtime-injected selector (lottie/canvas/swiper) is legitimately absent
    from the static capture; it must NOT be waved through as an absent-page skip
    — it keeps its normal not-found FAIL so a genuinely dead lottie is caught."""
    spec = {"transitions": [{"id": "r", "trigger": "load", "target": ".lottie-intro canvas"}]}
    obs = {"r": {"found": False, "before": {}, "after": {}}}
    art = evaluate(spec, obs, {}, ref_structure=_REF_STRUCTURE)
    assert art["entries"][0]["status"] == "fail", art["entries"][0]
