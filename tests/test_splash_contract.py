"""The splash absence certificate: one rule, read from the samples it rests on.

Two failure modes bracket this rule. `stateCount == 1` refused any page with
entry choreography (navercorp.com/tech/innovation: 13 states, no overlay).
Dropping that condition certified a page whose loader the overlay probe cannot
see (`is-loading` -> `loaded`, DOM 1000 -> 4000, every overlay record the
all-zero default). Each test here fails if the rule slides back toward either.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ui_clone.splash_contract import (
    COVERING_ENTER,
    COVERING_EXIT,
    COVERING_RECORD_FLOOR,
    AbsenceEvidence,
    absence_evidence,
    certify_absence,
    covering_exits,
    is_authoritative_absence,
    root_classes_removed,
    structural_shift,
)

WRAPPER = "body > div:nth-of-type(1)"


def _state(
    ts_ms: int,
    *,
    body_class: str = "",
    html_class: str = "",
    dom_length: int = 1000,
    covering: dict[str, float] | None = None,
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ts_ms": ts_ms,
        "hash": ts_ms + 1,
        "bodyClass": body_class,
        "htmlClass": html_class,
        "domLength": dom_length,
        "overlay": overlay
        if overlay is not None
        else {"selector": None, "identity": None, "coverage": 0, "visible": False, "opacity": "0"},
        "covering": {WRAPPER: 1.0} if covering is None else covering,
    }


def _clean_evidence(**overrides: Any) -> AbsenceEvidence:
    base: dict[str, Any] = {
        "capture_mode": "pre-navigation",
        "state_count": 13,
        "timed_out": False,
        "overlay_ever_visible": False,
        "covering_surveyed": True,
        "covering_exits": (),
        "root_classes_removed": (),
        "structural_shift": False,
    }
    base.update(overrides)
    return AbsenceEvidence(**base)


# ── certify_absence: the producer's rule ────────────────────────────────────


def test_many_states_with_every_channel_empty_certify() -> None:
    """The navercorp shape. Sample count is not a reason to refuse."""
    assert certify_absence(_clean_evidence(state_count=13)) is True
    assert certify_absence(_clean_evidence(state_count=1)) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture_mode", "reuse-session"),
        ("capture_mode", None),
        ("state_count", 0),
        ("timed_out", True),
        ("overlay_ever_visible", True),
        ("covering_surveyed", False),
        ("covering_exits", ("#loader",)),
        ("root_classes_removed", ("body.is-loading",)),
        ("structural_shift", True),
    ],
)
def test_any_single_channel_firing_blocks_certification(field: str, value: Any) -> None:
    assert certify_absence(_clean_evidence(**{field: value})) is False


# ── absence_evidence: reading the channels out of the samples ───────────────


def test_navercorp_tech_innovation_shape_certifies() -> None:
    """13 samples driven by entry animations and media readiness, classes empty
    throughout, DOM +2%, overlay never visible, wrappers cover throughout."""
    states = [
        _state(ts, dom_length=length)
        for ts, length in (
            (0, 121759), (127, 121759), (559, 122542), (676, 122548), (1021, 124130),
            (1146, 124130), (1489, 124130), (1604, 124130), (2187, 124130),
            (2655, 124130), (3573, 124130), (3804, 124130), (4154, 124262),
        )
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.to_contract() == {
        "overlayEverVisible": False,
        "coveringSurveyed": True,
        "coveringExits": [],
        "rootClassesRemoved": [],
        "structuralShift": False,
    }
    assert certify_absence(evidence) is True


def test_loading_class_lifecycle_with_blind_probe_does_not_certify() -> None:
    """The over-certification reproduction: the overlay probe saw nothing, but
    the root class and DOM length tell the loader story on their own."""
    states = [
        _state(0, body_class="is-loading", dom_length=1000),
        _state(700, body_class="is-loading", dom_length=1000),
        _state(1400, body_class="loaded", dom_length=4000),
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.overlay_ever_visible is False
    assert evidence.root_classes_removed == ("body.is-loading",)
    assert evidence.structural_shift is True
    assert certify_absence(evidence) is False


def test_in_flow_loader_that_leaves_the_viewport_does_not_certify() -> None:
    """A static/relative full-viewport loader: no overlay record, no class flip,
    DOM length nearly unchanged. Only its coverage lifecycle gives it away."""
    states = [
        _state(0, dom_length=1200, covering={WRAPPER: 1.0, "#loader": 1.0}),
        _state(900, dom_length=1210, covering={WRAPPER: 1.0}),
        _state(2900, dom_length=1210, covering={WRAPPER: 1.0, "#hero": 0.9}),
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.overlay_ever_visible is False
    assert evidence.root_classes_removed == ()
    assert evidence.structural_shift is False
    assert evidence.covering_exits == ("#loader",)
    assert certify_absence(evidence) is False


def test_low_z_absolute_overlay_and_shadow_root_splash_are_seen_through_covering() -> None:
    """Both shapes the probe is blind to reach the rule the same way: as an
    identity that covered the viewport and then did not."""
    shadow = "app-shell >>> div:nth-of-type(1)"
    states = [
        _state(0, covering={WRAPPER: 1.0, "#curtain": 1.0, shadow: 0.98}),
        _state(1200, covering={WRAPPER: 1.0, shadow: 0.98}),
        _state(3200, covering={WRAPPER: 1.0}),
    ]
    _, exits = covering_exits(states)
    assert exits == ("#curtain", shadow)


def test_hero_reflow_inside_hysteresis_is_not_an_exit() -> None:
    """76% -> 72% after fonts load must not read as a loader leaving."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#hero": 0.76}),
        _state(600, covering={WRAPPER: 1.0, "#hero": 0.72}),
        _state(2600, covering={WRAPPER: 1.0, "#hero": 0.72}),
    ]
    surveyed, exits = covering_exits(states)
    assert surveyed is True
    assert exits == ()
    assert certify_absence(absence_evidence(states, capture_mode="pre-navigation", timed_out=False))


def test_covering_below_the_exit_line_counts_as_exit_and_is_reported_once() -> None:
    """A dip below COVERING_EXIT is an exit even if the element comes back, and
    it is reported once."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#hero": 0.8}),
        _state(600, covering={WRAPPER: 1.0, "#hero": 0.1}),
        _state(1200, covering={WRAPPER: 1.0, "#hero": 0.8}),
        _state(2600, covering={WRAPPER: 1.0}),
    ]
    assert covering_exits(states) == (True, ("#hero",))


def test_a_curtain_that_settles_between_the_probe_floor_and_the_exit_line_is_an_exit() -> None:
    """The defect this pins: an exit line pinned down to the probe's 20%
    candidate floor called a full-viewport intro that settles at 35% "still
    present" and certified the page as splash-free. To the probe that overlay
    has never exited (a FAIL); to the certificate a missing exit means the
    page has no splash. Anything the probe would still be watching must refuse
    here."""
    for settled in (0.49, 0.35, 0.21, 0.1):
        states = [
            _state(0, covering={WRAPPER: 1.0, ".intro": 1.0}),
            _state(600, covering={WRAPPER: 1.0, ".intro": settled}),
            _state(2600, covering={WRAPPER: 1.0, ".intro": settled}),
        ]
        surveyed, exits = covering_exits(states)
        assert (surveyed, exits) == (True, (".intro",)), f"settled at {settled}"
        assert not certify_absence(
            absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
        ), f"settled at {settled}"


def test_an_element_that_never_moves_below_the_exit_line_is_not_an_exit() -> None:
    """COVERING_ENTER (45%) now sits below COVERING_EXIT (50%), so the drop is
    measured against the identity's own peak. A banner that simply sits at 47%
    for the whole capture has not left anything, and a page built out of such
    elements must still certify."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#banner": 0.47, "#aside": 0.46}),
        _state(600, covering={WRAPPER: 1.0, "#banner": 0.47, "#aside": 0.46}),
        _state(2600, covering={WRAPPER: 1.0, "#banner": 0.47, "#aside": 0.46}),
    ]
    surveyed, exits = covering_exits(states)
    assert (surveyed, exits) == (True, ())
    assert certify_absence(absence_evidence(states, capture_mode="pre-navigation", timed_out=False))


def test_a_mount_line_overlay_that_leaves_is_still_an_exit() -> None:
    """The peak anchor must not spare something that actually left: an overlay
    that mounts at the probe's 45% line and then vanishes still exits."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#curtain": 0.46}),
        _state(600, covering={WRAPPER: 1.0}),
    ]
    assert covering_exits(states) == (True, ("#curtain",))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_covering_thresholds_are_the_lifecycle_probes_thresholds() -> None:
    """The sweep above proves the pair agree today; this names the two numbers
    they must keep agreeing on, so a change to either side fails here with the
    other side's value in the message.

    Only the two INTAKE numbers are pinned. The exit line is not: below the
    probe's candidate floor the probe stops seeing an overlay at all, so
    lowering the exit line to meet it would make the certificate certify
    pages the probe would still be flagging. It stays at the half-viewport
    line it has always had."""
    proc = subprocess.run(
        [
            "node",
            "-e",
            f"""
            const probe = require({json.dumps(str(_PROBE_JS))});
            process.stdout.write(JSON.stringify({{
              enter: probe.MIN_INITIAL_COVERAGE_RATIO, floor: probe.MIN_AREA_RATIO,
            }}));
            """,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    probe_thresholds = json.loads(proc.stdout)
    assert (COVERING_ENTER, COVERING_RECORD_FLOOR) == (
        probe_thresholds["enter"],
        probe_thresholds["floor"],
    )
    assert COVERING_EXIT >= 0.5, (
        "the exit line may only move up: an identity that reached "
        f"{COVERING_ENTER} and later sits below {COVERING_EXIT} must still be "
        "an exit, or the certificate certifies what the probe would FAIL"
    )
    assert COVERING_RECORD_FLOOR <= COVERING_ENTER, (
        "the sampler must record the whole band the probe can see, or "
        "COVERING_ENTER is unreachable"
    )


def test_element_entering_the_viewport_is_not_an_exit() -> None:
    """A full-viewport section fading in (0.05 -> 1) enters; only leaving counts."""
    states = [
        _state(0, covering={WRAPPER: 1.0}),
        _state(800, covering={WRAPPER: 1.0, "#hero": 1.0}),
    ]
    assert covering_exits(states) == (True, ())


def test_sample_without_covering_survey_marks_run_unsurveyed() -> None:
    """A payload that never surveyed covering elements cannot certify by
    omission; the sampler always writes the field, so this only bites
    hand-built or older payloads."""
    states = [_state(0)]
    del states[0]["covering"]
    surveyed, exits = covering_exits(states)
    assert (surveyed, exits) == (False, ())
    assert certify_absence(absence_evidence(states, capture_mode="pre-navigation", timed_out=False)) is False


def test_root_class_removed_or_replaced_is_a_loading_lifecycle() -> None:
    assert root_classes_removed([
        _state(0, body_class="is-loading"), _state(900, body_class="loaded"),
    ]) == ("body.is-loading",)
    assert root_classes_removed([
        _state(0, html_class="wf-loading no-js"), _state(900, html_class="wf-active no-js"),
    ]) == ("html.wf-loading",)
    assert root_classes_removed([
        _state(0, html_class="preload"), _state(900, html_class=""),
    ]) == ("html.preload",)


def test_root_class_only_added_is_setup_finishing_not_a_splash() -> None:
    """Lenis / hydration markers appear after the first sample on pages with
    no splash at all; they must not cost the certificate."""
    states = [
        _state(0, html_class="", body_class=""),
        _state(400, html_class="lenis lenis-smooth", body_class="is-ready"),
        _state(2400, html_class="lenis lenis-smooth", body_class="is-ready"),
    ]
    assert root_classes_removed(states) == ()
    assert certify_absence(absence_evidence(states, capture_mode="pre-navigation", timed_out=False)) is True


def test_root_class_present_at_first_sample_and_removed_later_counts_even_if_restored() -> None:
    states = [
        _state(0, body_class="is-loading"),
        _state(500, body_class=""),
        _state(2500, body_class="is-loading"),
    ]
    assert root_classes_removed(states) == ("body.is-loading",)


def test_structural_shift_uses_the_samplers_moving_baseline() -> None:
    """Gradual growth under 20% per step never trips it; one 30% step does."""
    assert structural_shift([_state(0, dom_length=1000), _state(1, dom_length=1150), _state(2, dom_length=1190)]) is False
    assert structural_shift([_state(0, dom_length=1000), _state(1, dom_length=1300)]) is True
    assert structural_shift([_state(0, dom_length=4000), _state(1, dom_length=1000)]) is True
    # navercorp: 121759 -> 124262 is ~2%.
    assert structural_shift([_state(0, dom_length=121759), _state(1, dom_length=124262)]) is False


def test_overlay_predicate_matches_the_probe() -> None:
    visible = {"selector": "#intro", "coverage": 0.98, "visible": True, "opacity": "1"}
    states = [_state(0, overlay=visible), _state(900)]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.overlay_ever_visible is True
    assert certify_absence(evidence) is False
    # visible without coverage is the default record's shape, not a sighting.
    zero = {"selector": None, "coverage": 0, "visible": True, "opacity": "0"}
    assert absence_evidence([_state(0, overlay=zero)], capture_mode="pre-navigation", timed_out=False).overlay_ever_visible is False


def test_evidence_records_capture_mode_and_timeout_verbatim() -> None:
    evidence = absence_evidence([_state(0)], capture_mode="reuse-session", timed_out=True)
    assert evidence.capture_mode == "reuse-session"
    assert evidence.timed_out is True
    assert evidence.state_count == 1
    assert certify_absence(evidence) is False


# ── is_authoritative_absence: what consumers read ───────────────────────────


def _stamped(stamp: bool | None, *, capture_mode: str | None = "pre-navigation") -> dict[str, Any]:
    contract: dict[str, Any] = {
        "schemaVersion": 1,
        "detected": False,
        "overlay": {"everVisible": False, "maxCoverage": 0},
        "capture": {"stateCount": 13, "timedOut": False, "reason": "stable-2s"},
    }
    if capture_mode is not None:
        contract["captureMode"] = capture_mode
    if stamp is not None:
        contract["capture"]["authoritativeNegative"] = stamp
    return contract


def test_stamp_is_read_in_both_directions() -> None:
    assert is_authoritative_absence(_stamped(True)) is True
    assert is_authoritative_absence(_stamped(False)) is False


def test_stamp_is_never_re_derived_from_metadata() -> None:
    """A stamped False with all-clear metadata stays False (an artifact keeps
    the verdict it was produced under); metadata without a stamp fails closed,
    because no producer generation ever wrote that shape."""
    assert is_authoritative_absence(_stamped(False)) is False
    assert is_authoritative_absence(_stamped(None)) is False
    unstamped_single = _stamped(None)
    unstamped_single["capture"]["stateCount"] = 1
    assert is_authoritative_absence(unstamped_single) is False


@pytest.mark.parametrize("capture_mode", ["reuse-session", None, "post-navigation"])
def test_stamped_true_requires_pre_navigation_like_the_producer(capture_mode: str | None) -> None:
    assert is_authoritative_absence(_stamped(True, capture_mode=capture_mode)) is False


def test_detected_or_unversioned_contracts_never_certify_absence() -> None:
    detected = _stamped(True)
    detected["detected"] = True
    assert is_authoritative_absence(detected) is False
    unversioned = _stamped(True)
    del unversioned["schemaVersion"]
    assert is_authoritative_absence(unversioned) is False


def test_pre_metadata_artifact_keeps_its_historical_reading() -> None:
    """Before 0.7.30 the contract carried no probe metadata and every consumer
    read pre-navigation `detected: false` as authoritative; reuse-session fell
    through. Both readings survive for those artifacts."""
    assert is_authoritative_absence({"schemaVersion": 1, "detected": False}) is True
    assert is_authoritative_absence({
        "schemaVersion": 1, "detected": False, "captureMode": "pre-navigation",
        "overlay": {"selector": None, "maxCoverage": 0, "exitObserved": False},
    }) is True
    assert is_authoritative_absence({
        "schemaVersion": 1, "detected": False, "captureMode": "reuse-session",
    }) is False


def test_malformed_input_fails_closed() -> None:
    assert is_authoritative_absence(None) is False
    assert is_authoritative_absence([]) is False
    assert is_authoritative_absence({}) is False
    broken = _stamped(True)
    broken["capture"] = "yes"
    assert is_authoritative_absence(broken) is False


# ── holes in the widened certificate (adversarial review of 26cc577) ────────


def test_loading_class_added_after_first_sample_and_removed_later_is_not_certified() -> None:
    """A deferred loader: `` -> `is-loading` -> `loaded`, DOM +15% (under the
    structural threshold), overlay never visible, wrapper covers throughout.
    Anchoring root-class removal on the first sample alone certified this."""
    states = [
        _state(0, body_class="", dom_length=1000),
        _state(300, body_class="is-loading", dom_length=1000),
        _state(1200, body_class="loaded", dom_length=1150),
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.structural_shift is False
    assert evidence.root_classes_removed == ("body.is-loading",)
    assert certify_absence(evidence) is False


@pytest.mark.parametrize("coverage", [0.45, 0.5, 0.6, 0.74])
def test_loader_at_a_coverage_the_lifecycle_probe_accepts_is_not_certified(coverage: float) -> None:
    """splash-lifecycle-probe.js judges an overlay covering >= 45% of the
    viewport as a splash. A loader in that band that leaves must therefore
    count as a covering exit here, or the two components disagree about what a
    splash is and the certificate suppresses the check that would have seen it."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#loader": coverage}),
        _state(800, covering={WRAPPER: 1.0}),
        _state(2800, covering={WRAPPER: 1.0}),
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.covering_exits == ("#loader",)
    assert certify_absence(evidence) is False


def test_page_with_persistent_partial_wrappers_and_hysteresis_still_certifies() -> None:
    """Widening the exit rule must not make the certificate useless: nested
    wrappers that never leave, a hero that reflows inside the hysteresis band
    (0.9 -> 0.84) once fonts land, a banner parked below the exit line, a
    below-record-floor nav, a section fading in, and setup classes landing
    late are what a page with no splash looks like.

    A hero falling 0.5 -> 0.3 used to be listed here as hysteresis too. It no
    longer certifies, and must not: at 0.3 the lifecycle probe still sees a
    candidate that never exited, so "nothing here to look at" would be a false
    statement about that page."""
    states = [
        _state(0, covering={WRAPPER: 1.0, "#main": 0.9, "#banner": 0.47, "#nav": 0.22}),
        _state(400, html_class="lenis lenis-smooth", covering={WRAPPER: 1.0, "#main": 0.84, "#banner": 0.47, "#nav": 0.22}),
        _state(2400, html_class="lenis lenis-smooth", covering={WRAPPER: 1.0, "#main": 0.84, "#banner": 0.47, "#nav": 0.22, "#reveal": 0.6}),
    ]
    evidence = absence_evidence(states, capture_mode="pre-navigation", timed_out=False)
    assert evidence.covering_exits == ()
    assert evidence.root_classes_removed == ()
    assert certify_absence(evidence) is True


_PROBE_JS = Path(__file__).resolve().parents[1] / "skills" / "visual-debug" / "scripts" / "lib" / "splash-lifecycle-probe.js"


def _probe_verdict(ref_samples: list[dict[str, Any]], impl_samples: list[dict[str, Any]]) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "node",
            "-e",
            f"""
            const probe = require({json.dumps(str(_PROBE_JS))});
            process.stdout.write(JSON.stringify(
              probe.compareLifecycles({json.dumps(ref_samples)}, {json.dumps(impl_samples)})
            ));
            """,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # type: ignore[no-any-return]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_certificate_refuses_every_overlay_coverage_the_lifecycle_probe_accepts() -> None:
    """The property behind the threshold: whatever share of the viewport the
    lifecycle probe accepts as a mounted splash (no `ref-coverage-too-low`),
    the certificate must refuse when an element at that share leaves. Both are
    exercised through their real entry points, so the two cannot drift apart
    without this test noticing."""
    accepted: list[float] = []
    for step in range(1, 21):
        coverage = step / 20
        overlay = {
            "selector": "#loader",
            "signature": "loading",
            "rect": {"x": 0, "y": 0, "width": 1280 * coverage, "height": 800},
            "coverageRatio": coverage,
            "opacity": 1,
            "transform": "",
        }
        ref: list[dict[str, Any]] = [
            {"t": 0, "overlay": overlay, "viewportMotion": 0.0},
            {"t": 50, "overlay": overlay, "viewportMotion": 0.0},
            {"t": 400, "overlay": None, "viewportMotion": 0.0},
            {"t": 450, "overlay": None, "viewportMotion": 0.0},
        ]
        verdict = _probe_verdict(ref, ref)
        probe_calls_it_a_splash = (
            verdict["ref"]["mounted"] and "ref-coverage-too-low" not in verdict["violations"]
        )
        states = [
            _state(0, covering={WRAPPER: 1.0, "#loader": coverage}),
            _state(800, covering={WRAPPER: 1.0}),
            _state(2800, covering={WRAPPER: 1.0}),
        ]
        certified = certify_absence(absence_evidence(states, capture_mode="pre-navigation", timed_out=False))
        if probe_calls_it_a_splash:
            accepted.append(coverage)
            assert certified is False, f"probe accepts a {coverage:.2f} splash; certificate certified absence"
    assert accepted, "the probe accepted no coverage at all; the sweep proves nothing"
    assert min(accepted) < 0.75, "the sweep never reached the band below the old 0.75 enter threshold"
