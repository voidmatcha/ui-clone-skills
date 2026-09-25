"""The absence certificate of `states/splash/contract.json`, in one place.

`capture.authoritativeNegative` answers: did the Phase A capture look for a
splash across the whole first load and find nothing that looks like one? The
producer (`scripts/extract/capture-states.sh`) derives it here from the sampled
states and stamps it; the consumers (`state-structure-spec.py`,
`generation_plan.py`, `verification-plan.sh`) read the stamp through
`is_authoritative_absence`. Neither side carries a private copy of the rule.

What the certificate rests on
-----------------------------
The overlay probe (`position: fixed`, or `absolute` with z-index >= 10, covering
>= 75% of the viewport) is blind to loaders it cannot tell from page content: an
in-flow full-viewport shell, an absolute overlay below z-index 10. A single
sample cannot classify those - a hero section and an intro curtain are both
full-viewport blocks - so the certificate reads their LIFECYCLE instead, from
channels the sampler records at every state:

* `overlay`            - the probe never saw a visible overlay.
* `covering`           - no element that covered the viewport later left it.
* html/body className  - no class token seen on an earlier sample was missing
                         from a later one (`is-loading` -> `loaded`,
                         `wf-loading` -> `wf-active`, `preload` -> ``, and a
                         deferred `` -> `is-loading` -> `loaded`). Tokens that
                         are only ADDED (`lenis`, `is-ready`) mark setup
                         finishing, which pages without any splash do too.
* visibleStructure     - no material replacement of rendered structure inside
                         the viewport (a loading shell replaced by content).
                         Whole-document length remains a legacy fallback only.

Plus the capture itself: attached before navigation (nothing before the first
sample was missed) and its splash-relevant channels reached their own settle.
The broader evidence recorder may continue to its wall-clock cap for periodic
motion without weakening that lifecycle result.

What it does NOT rest on: the number of samples. A page whose entry
choreography walks through many class states has looked for a splash just as
many times; that was the old `len(states) == 1` mistake.

What "covering" means is not this module's to choose. The check this
certificate can keep from running, `splash-lifecycle-check.sh`, judges a
reference overlay through `splash-lifecycle-probe.js`: an element is a splash
candidate from `MIN_AREA_RATIO` (20%) of the viewport and a mounted splash from
`MIN_INITIAL_COVERAGE_RATIO` (45%). The certificate must refuse everything the
probe would accept, so `COVERING_ENTER` and `COVERING_RECORD_FLOOR` below ARE
those two numbers; the sampler in capture-states.sh takes them from here at run
time, and tests/test_splash_contract.py sweeps the probe to prove they agree.

`COVERING_EXIT` is NOT pinned to the probe, and the direction of the argument
is why. Below `MIN_AREA_RATIO` the probe stops seeing a candidate, so an
overlay settled at 35% is still PRESENT to it - it would report
`overlay-never-exited`, a FAIL. To this certificate "no exit recorded" means
ABSENCE, so pinning the exit line down to the candidate floor does not make the
certificate refuse what the probe would flag; it makes the certificate certify
MORE. The exit line is the writer's own, and it stays where it has always been.

What it still cannot see, stated plainly: a splash shorter than one poll
interval, and a reveal that changes no bounding box, class, or DOM length
(a clip-path wipe over a persistent element). That is why the certificate is
allowed to override only same-capture or generic load signals downstream, never
a detector that reads bundle source or a DOM diff. When such a detector does
dispatch the lifecycle check against a page whose reference shows no overlay,
`ref-overlay-absent` stays a FAIL: the check reads this certificate only to say
which reference measurement that FAIL is about (`refAbsence.guidance`). The
check's probe and the sampler behind this certificate both enumerate elements
and read `getComputedStyle(el)` without a pseudo-element argument, so a curtain
neither can see (`html:not(.loaded)::before`) certifies here and passes there;
their agreement is one blind spot counted twice, not a second measurement.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# An element counts as covering the viewport at this share. Equal to the
# lifecycle probe's MIN_INITIAL_COVERAGE_RATIO: the smallest overlay it will
# call a mounted splash on the reference.
COVERING_ENTER = 0.45
# ... and has left it once it drops below this share (or disappears), having
# been higher before. Deliberately NOT the probe's MIN_AREA_RATIO: see the
# module docstring. Half the viewport is where a full-screen curtain has
# visibly stopped being a curtain.
COVERING_EXIT = 0.5
# The sampler records every rendered element at or above this share. Equal to
# the probe's MIN_AREA_RATIO, below which it no longer sees a candidate at all,
# and INDEPENDENT of COVERING_EXIT: recording the whole band the probe can see
# is what lets the writer read a settle at 35% as a measured share instead of
# an absence, and is what keeps COVERING_ENTER's 45% line reachable at all.
COVERING_RECORD_FLOOR = 0.20
# Same threshold the sampler uses for `structuralDelta`.
STRUCTURAL_SHIFT = 0.2


@dataclass(frozen=True)
class AbsenceEvidence:
    """Everything `certify_absence` looks at, as recorded in the artifact."""

    capture_mode: str | None
    state_count: int
    timed_out: bool
    overlay_ever_visible: bool
    # Every recorded state carried a `covering` survey. The sampler always
    # writes one, so for its own output this is a schema guard, not evidence:
    # it keeps a payload that never surveyed covering elements (hand-built,
    # or from an older sampler) from certifying by omission.
    covering_surveyed: bool
    covering_exits: tuple[str, ...]
    root_classes_removed: tuple[str, ...]
    structural_shift: bool

    def to_contract(self) -> dict[str, Any]:
        return {
            "overlayEverVisible": self.overlay_ever_visible,
            "coveringSurveyed": self.covering_surveyed,
            "coveringExits": list(self.covering_exits),
            "rootClassesRemoved": list(self.root_classes_removed),
            "structuralShift": self.structural_shift,
        }


def certify_absence(evidence: AbsenceEvidence) -> bool:
    """The producer's rule: True only when every channel came back empty."""
    return (
        evidence.capture_mode == "pre-navigation"
        and evidence.state_count >= 1
        and not evidence.timed_out
        and not evidence.overlay_ever_visible
        and evidence.covering_surveyed
        and not evidence.covering_exits
        and not evidence.root_classes_removed
        and not evidence.structural_shift
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tokens(value: Any) -> set[str]:
    return set(str(value or "").split())


def overlay_ever_visible(states: Iterable[dict[str, Any]]) -> bool:
    """The probe's own predicate: a visible record with non-zero coverage."""
    for state in states:
        overlay = state.get("overlay")
        if isinstance(overlay, dict) and overlay.get("visible") and _num(overlay.get("coverage")) > 0:
            return True
    return False


def covering_exits(states: list[dict[str, Any]]) -> tuple[bool, tuple[str, ...]]:
    """(surveyed, exits): identities that covered the viewport and later left.

    An identity enters once its share reaches COVERING_ENTER and has exited at
    the first later sample where it is below COVERING_EXIT or absent, having
    stood higher at some earlier sample. Each identity is reported once.

    The drop is measured against the identity's own peak rather than a fixed
    band below COVERING_ENTER, because COVERING_ENTER (45%, the probe's mount
    line) now sits BELOW COVERING_EXIT (50%). Without the peak an element that
    simply sits at 47% for the whole capture would enter and then read as an
    exit on the very next sample. With it, only a share that actually fell
    counts - which is what hysteresis was ever protecting.
    """
    surveyed = True
    peaks: dict[str, float] = {}
    exits: list[str] = []
    for state in states:
        covering = state.get("covering")
        if not isinstance(covering, dict):
            surveyed = False
            continue
        for identity, peak in peaks.items():
            share = _num(covering.get(identity))
            if share < COVERING_EXIT and share < peak and identity not in exits:
                exits.append(identity)
        for identity, share in covering.items():
            value = _num(share)
            if value >= COVERING_ENTER:
                key = str(identity)
                peaks[key] = max(peaks.get(key, 0.0), value)
    return surveyed, tuple(exits)


def root_classes_removed(states: list[dict[str, Any]]) -> tuple[str, ...]:
    """Class tokens on html/body that some earlier sample carried and a later
    sample lacks.

    Anchored on every earlier sample, not only the first: a loader whose gating
    class lands after the first poll (`` -> `is-loading` -> `loaded`) removes a
    token the first sample never had, and is exactly the deferred shape most
    likely to be missed.
    """
    removed: list[str] = []
    for element, key in (("html", "htmlClass"), ("body", "bodyClass")):
        seen: set[str] = set()
        for state in states:
            tokens = _tokens(state.get(key))
            for token in sorted(seen - tokens):
                label = f"{element}.{token}"
                if label not in removed:
                    removed.append(label)
            seen |= tokens
    return tuple(removed)


def structural_shift(states: list[dict[str, Any]]) -> bool:
    """Whether visible page structure materially changed during capture.

    Current sampler output carries an explicit viewport-scoped structure
    channel. This excludes offscreen SSR pruning and other whole-document
    churn that cannot be a visible loading-shell replacement. Older artifacts
    lack that channel, so retain their historical domLength fallback.
    """
    if not states:
        return False
    has_visible_channel = any("visibleStructure" in state for state in states)
    if has_visible_channel:
        visible = [state.get("visibleStructure") for state in states]
        valid = all(
            isinstance(value, dict)
            and isinstance(value.get("changed"), bool)
            and isinstance(value.get("hash"), str)
            and bool(value.get("hash"))
            and isinstance(value.get("count"), int)
            and value.get("count", -1) >= 0
            for value in visible
        )
        if not valid:
            # A partial current-generation channel cannot prove absence. Older
            # artifacts have no visibleStructure key at all and use the legacy
            # domLength fallback below.
            return True
        return any(value["changed"] for value in visible if isinstance(value, dict))
    baseline = _num(states[0].get("domLength"))
    for state in states[1:]:
        length = _num(state.get("domLength"))
        if abs(length - baseline) / max(baseline, 1.0) > STRUCTURAL_SHIFT:
            return True
    return False


def absence_evidence(
    states: list[dict[str, Any]],
    *,
    capture_mode: str | None,
    timed_out: bool,
) -> AbsenceEvidence:
    """Read every channel out of the sampled states."""
    surveyed, exits = covering_exits(states)
    return AbsenceEvidence(
        capture_mode=capture_mode,
        state_count=len(states),
        timed_out=bool(timed_out),
        overlay_ever_visible=overlay_ever_visible(states),
        covering_surveyed=surveyed,
        covering_exits=exits,
        root_classes_removed=root_classes_removed(states),
        structural_shift=structural_shift(states),
    )


def is_authoritative_absence(contract: Any) -> bool:
    """Whether a contract.json certifies that the page has no splash.

    Reads the producer's stamp; never re-derives it. An artifact keeps the
    verdict it was produced under, in both directions.

    Two generations exist in the wild. Since 0.7.30 the contract carries
    `overlay.everVisible`, `capture.*` and the stamp together (they landed in
    one commit), so a contract with capture metadata but no stamp was never
    produced and fails closed. Before that the contract had neither, and every
    consumer read a pre-navigation `detected: false` as authoritative; that
    reading is kept for those artifacts. The producer only ever stamps True for
    `captureMode == "pre-navigation"`; the same allow-list is applied here so a
    hand-built stamp cannot bypass it. The legacy shape predates `captureMode`
    and keeps its historical deny-list (`reuse-session` falls through).
    """
    if not isinstance(contract, dict):
        return False
    if contract.get("schemaVersion") is None:
        return False
    if contract.get("detected") is not False:
        return False
    capture_mode = contract.get("captureMode")
    if capture_mode == "reuse-session":
        return False

    overlay = contract.get("overlay")
    capture = contract.get("capture")
    has_overlay_metadata = isinstance(overlay, dict) and "everVisible" in overlay
    has_capture_metadata = isinstance(capture, dict)
    if not has_overlay_metadata and not has_capture_metadata:
        # Pre-0.7.30 artifact: no probe metadata was ever written for it.
        return True
    if not isinstance(capture, dict):
        return False
    return capture.get("authoritativeNegative") is True and capture_mode == "pre-navigation"
