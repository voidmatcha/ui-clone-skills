"""ui_clone.gates.hover_probe — per-entry fallback probe for hover coverage.

Loop-9 regression class: hover-state-compare capped at 5 targets, all 5
ended documented known-skips, 0 runs were measured — and the gate PASSED
("all 0 hover target-run(s) within SSIM threshold"). Meanwhile the nav pill
label expansion (bundle: width 0 → auto spring on hover) was missing from
the impl entirely (labels baked width:0). Every hoverable entry now needs
either >=1 measured run or a fallback probe verdict; an all-skip run can
never count as PASS.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.hover_probe import build_plan, evaluate_entry

HOVER_ENTRY = {
    "id": "hover-nav-buttons",
    "trigger": "hover",
    "target": ".nav_dot_button",
    "animation": {"property": "background-color, color"},
}

# State-flag spring (animate:{width:a?"auto":0}): a STATE reveal that the
# extractor double-buckets into hoverSizeExpansions AND activeStateExpansions.
# build_plan must de-dup it out of the hover plan (loop-e2e-12 false positive).
EXPANSION = {
    "kind": "size-expansion",
    "classToken": "label_container",
    "resolvedClassName": "nav_label_container__okVKb",
    "property": "width",
    "from": "0",
    "to": 'a?"auto":0',
    "transition": 'type:"spring",stiffness:120,damping:20',
    "source": "bundles/page-x.js",
}

# TRUE hover spring (animate:{width:"auto"}, no state flag): always probed, never
# de-duped — the canonical hover-probe fixture for the verdict/CLI tests.
TRUE_HOVER_EXPANSION = {
    "kind": "size-expansion",
    "classToken": "label_container",
    "resolvedClassName": "nav_label_container__okVKb",
    "property": "width",
    "from": "0",
    "to": '"auto"',
    "transition": 'type:"spring",stiffness:120,damping:20',
    "source": "bundles/page-x.js",
}


def _ref(tmp_path: Path, *, expansions: bool = True) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [HOVER_ENTRY],
    }))
    extraction: dict = {"schemaVersion": 1, "extractions": {}}
    if expansions:
        extraction["extractions"]["hoverSizeExpansions"] = [TRUE_HOVER_EXPANSION]
    (ref / "bundle-extraction.json").write_text(json.dumps(extraction))
    return ref


# ── plan ───────────────────────────────────────────────────────────────


def test_plan_includes_spec_hover_entries_and_expansions(tmp_path: Path) -> None:
    plan = build_plan(_ref(tmp_path))
    ids = {p["id"] for p in plan}
    assert "hover-nav-buttons" in ids
    assert any("label_container" in i for i in ids)
    expansion = next(p for p in plan if "label_container" in p["id"])
    assert expansion["channels"] == ["size"]
    assert ".nav_label_container__okVKb" in expansion["selectors"]


def test_plan_channels_from_property(tmp_path: Path) -> None:
    plan = build_plan(_ref(tmp_path, expansions=False))
    nav = next(p for p in plan if p["id"] == "hover-nav-buttons")
    assert nav["channels"] == ["color"]


def test_state_flag_spring_deduped_out_of_hover_plan(tmp_path: Path) -> None:
    """loop-e2e-12 false-positive guard: a width spring gated on a state flag
    (animate:{width:a?"auto":0}) is double-bucketed by the extractor into BOTH
    hoverSizeExpansions and activeStateExpansions. It is a STATE reveal (verified
    by the state-reveal gate), not a hover — build_plan must NOT forge a hover
    probe for it (neither ref nor clone has a :hover width rule, so the probe
    false-fails a faithful clone). A TRUE hover spring (only in hoverSizeExpansions)
    STILL gets a probe, so real hover defects keep failing — the fix is a
    correct re-classification, not a blanket suppression."""
    ref = tmp_path / "ref"
    ref.mkdir()
    ref.joinpath("transition-spec.json").write_text(json.dumps({"transitions": []}))
    ref.joinpath("bundle-extraction.json").write_text(json.dumps({
        "schemaVersion": 1,
        "extractions": {
            "hoverSizeExpansions": [
                # state-driven nav label: same class also in activeStateExpansions
                EXPANSION,
                # true hover chip: animate:{width:"auto"}, no state flag
                {
                    "classToken": "menu_chip",
                    "resolvedClassName": "hdr_menu_chip__QQ12",
                    "property": "width",
                    "from": "0",
                    "to": '"auto"',
                },
            ],
            "activeStateExpansions": [
                {
                    "classToken": "label_container",
                    "resolvedClassName": "nav_label_container__okVKb",
                    "property": "width",
                    "stateFlag": "a",
                    "to": "auto",
                },
            ],
        },
    }))
    ids = {p["id"] for p in build_plan(ref)}
    # state-driven label de-duped out -> no spurious hover probe -> no false fail
    assert "size-expansion:label_container" not in ids, ids
    # true hover spring survives -> still probed -> a dead impl still fails
    assert "size-expansion:menu_chip" in ids, ids


def test_same_class_true_hover_and_state_spring_keeps_hover_probe(tmp_path: Path) -> None:
    """P1 silent-bypass guard (review live-repro on commit 16f5007): when ONE
    class carries BOTH a genuine hover spring (animate:{width:"auto"}, no state
    flag) AND a state-flag spring (animate:{width:a?"auto":0}), and both land in
    hoverSizeExpansions, a CLASS-keyed de-dup drops the genuine hover entry too —
    leaving an empty plan, a status='skip' (RC 0), and a clone that silently
    dropped the real hover behavior. The de-dup must be ENTRY-SIGNATURE scoped:
    the state-flag entry is de-duped out, but the true-hover entry on the SAME
    class STILL produces a probe (so a dead clone still FAILs).

    The existing test_state_flag_spring_deduped_out_of_hover_plan puts the two
    springs on DIFFERENT classes, so it never exercises this collision."""
    ref = tmp_path / "ref"
    ref.mkdir()
    ref.joinpath("transition-spec.json").write_text(json.dumps({"transitions": []}))
    ref.joinpath("bundle-extraction.json").write_text(json.dumps({
        "schemaVersion": 1,
        "extractions": {
            "hoverSizeExpansions": [
                # state-flag spring on label_container (animate:{width:a?"auto":0})
                EXPANSION,
                # TRUE hover spring on the SAME class: animate:{width:"auto"},
                # no state-flag ternary — this is a real :hover/whileHover.
                {
                    "kind": "size-expansion",
                    "classToken": "label_container",
                    "resolvedClassName": "nav_label_container__okVKb",
                    "property": "width",
                    "from": "0",
                    "to": '"auto"',
                    "transition": 'type:"spring",stiffness:120,damping:20',
                },
            ],
            "activeStateExpansions": [
                {
                    "classToken": "label_container",
                    "resolvedClassName": "nav_label_container__okVKb",
                    "property": "width",
                    "stateFlag": "a",
                    "to": "auto",
                },
            ],
        },
    }))
    plan = build_plan(ref)
    # exactly one plan entry survives — the true-hover spring, not the state spring
    assert len(plan) == 1, plan
    entry = plan[0]
    assert entry["id"] == "size-expansion:label_container", entry
    # the surviving entry is a real probe: a dead sample (no width delta, no
    # :hover rule, element on-screen) must FAIL — not skip, never a silent pass.
    verdict = evaluate_entry(
        entry,
        {
            "found": True,
            "offScreen": False,
            "cssHoverProps": [],
            "before": {"width": 0.0, "bg": "rgb(1,1,1)", "color": "c1",
                       "opacity": 1.0, "transform": "none"},
            "after": {"width": 0.0, "bg": "rgb(1,1,1)", "color": "c1",
                      "opacity": 1.0, "transform": "none"},
        },
    )
    assert verdict["status"] == "fail", verdict


def test_same_class_true_numeric_hover_and_numeric_state_spring_keeps_hover_probe(
    tmp_path: Path,
) -> None:
    """Codex live-repro (HIGH): the signature-scoping handled `to="auto"` (quoted)
    but NOT NUMERIC reveals. The active extractor supports numeric `to` values
    (e.g. to="120" / to=120); the state-flag ternary the bundle serialises is then
    `a?"120":0` or `a?120:0`. When ONE class carries BOTH a genuine NUMERIC hover
    spring (animate:{width:"120"}, no state flag) AND a numeric state-flag spring,
    a de-dup that only matched QUOTED "auto" mis-handled the numeric pair: the
    genuine numeric-hover entry was dropped (or a state probe wrongly kept), so a
    clone that dropped a real numeric hover passed silently.

    Mirror of test_same_class_true_hover_and_state_spring_keeps_hover_probe with
    NUMERIC reveal values: the true numeric-hover entry must STILL produce a probe,
    and a dead on-screen sample must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    ref.joinpath("transition-spec.json").write_text(json.dumps({"transitions": []}))
    ref.joinpath("bundle-extraction.json").write_text(json.dumps({
        "schemaVersion": 1,
        "extractions": {
            "hoverSizeExpansions": [
                # NUMERIC state-flag spring on label_container (animate:{width:a?120:0}).
                # Unquoted numeric ternary — the generalized regex must match it.
                {
                    "kind": "size-expansion",
                    "classToken": "label_container",
                    "resolvedClassName": "nav_label_container__okVKb",
                    "property": "width",
                    "from": "0",
                    "to": "a?120:0",
                    "transition": 'type:"spring",stiffness:120,damping:20',
                },
                # TRUE NUMERIC hover spring on a DISTINCT class: animate:{width:120},
                # no state-flag ternary — a real :hover/whileHover numeric reveal.
                # Its bare 'to' (120) byte-equals the active tuple's RESOLVED value
                # (120); the de-dup must NOT drop it on that bare-value collision —
                # only the reconstructed raw ternary form should match an active tuple.
                # A distinct class makes the surviving id observable so a wrongly
                # dropped true-hover is caught (same class would yield an identical
                # dict to the state spring and mask the drop).
                {
                    "kind": "size-expansion",
                    "classToken": "menu_chip",
                    "resolvedClassName": "hdr_menu_chip__QQ12",
                    "property": "width",
                    "from": "0",
                    "to": "120",
                    "transition": 'type:"spring",stiffness:120,damping:20',
                },
            ],
            "activeStateExpansions": [
                {
                    "classToken": "label_container",
                    "resolvedClassName": "nav_label_container__okVKb",
                    "property": "width",
                    "stateFlag": "a",
                    "to": "120",
                },
            ],
        },
    }))
    plan = build_plan(ref)
    # exactly one plan entry survives — the true numeric-hover spring, not the state spring
    assert len(plan) == 1, plan
    entry = plan[0]
    assert entry["id"] == "size-expansion:menu_chip", entry
    # the surviving entry is a real probe: a dead on-screen sample (no width delta,
    # no :hover rule) must FAIL — not skip, never a silent pass.
    verdict = evaluate_entry(
        entry,
        {
            "found": True,
            "offScreen": False,
            "cssHoverProps": [],
            "before": {"width": 0.0, "bg": "rgb(1,1,1)", "color": "c1",
                       "opacity": 1.0, "transform": "none"},
            "after": {"width": 0.0, "bg": "rgb(1,1,1)", "color": "c1",
                      "opacity": 1.0, "transform": "none"},
        },
    )
    assert verdict["status"] == "fail", verdict


# ── verdicts ───────────────────────────────────────────────────────────


def _sample(
    *,
    found: bool = True,
    css_props: list[str] | None = None,
    width_before: float = 0.0,
    width_after: float = 0.0,
    bg_changed: bool = False,
) -> dict:
    return {
        "found": found,
        "cssHoverProps": css_props or [],
        "before": {"width": width_before, "bg": "rgb(1,1,1)", "color": "c1",
                   "opacity": 1.0, "transform": "none"},
        "after": {"width": width_after,
                  "bg": "rgb(9,9,9)" if bg_changed else "rgb(1,1,1)",
                  "color": "c1", "opacity": 1.0, "transform": "none"},
    }


def test_baked_zero_width_label_fails_probe() -> None:
    """The loop-9 defect: label containers baked width:0, no hover expansion
    via JS events, no CSS hover width rule — fallback probe must FAIL."""
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(entry, _sample(width_before=0.0, width_after=0.0))
    assert verdict["status"] == "fail"
    assert "size" in str(verdict["reason"])


def test_js_driven_expansion_verified() -> None:
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(entry, _sample(width_before=0.0, width_after=84.0))
    assert verdict["status"] == "verified"


def test_scroll_revealed_js_hover_transform_verified() -> None:
    # tools-batch-11 ITEM 3: a framer-style JS whileHover (transform) target that
    # was off-screen/scroll-revealed at idle. Once the probe scrolls it into view
    # (belowFoldOk) and dispatches real pointer events, the transform end-state
    # changes -> the verdict path is reached and the entry VERIFIES. (The shell
    # findVisible fix is what makes such a target resolve found:true; the verdict
    # logic is unchanged.) Paired with the FAIL guard below.
    entry = {"id": "deck", "selectors": [".resources_deck"], "channels": ["transform"]}
    s = _sample(found=True)
    s["after"]["transform"] = "matrix(1.25, 0, 0, 1.25, 0, 0)"
    verdict = evaluate_entry(entry, s)
    assert verdict["status"] == "verified", verdict


def test_ancestor_scale_transform_via_width_ratio_verified() -> None:
    # batch-12 ITEM 6: a framer whileHover scale applied to an ANCESTOR
    # (motion.button) leaves the probed img's OWN computed transform "none" while
    # its bounding-rect WIDTH grows by the scale factor (440 -> 453.2 = 1.03x). The
    # transform channel registers the rect-width scale as a delta -> verified.
    entry = {"id": "img", "selectors": [".broken_system img"], "channels": ["transform"]}
    s = _sample(found=True, width_before=440.0, width_after=453.2)  # transform none->none
    assert evaluate_entry(entry, s)["status"] == "verified", s


def test_transform_unchanged_and_no_width_scale_still_fails() -> None:
    # Detection guard: computed transform "none" unchanged AND no rect-width scale
    # (and no :hover rule) -> no delta -> the hover does not fire -> still FAILS.
    entry = {"id": "img", "selectors": [".broken_system img"], "channels": ["transform"]}
    s = _sample(found=True, width_before=440.0, width_after=440.0)
    assert evaluate_entry(entry, s)["status"] == "fail", s


def test_scroll_revealed_target_without_behavior_still_fails() -> None:
    # tools-batch-11 ITEM 3 regression guard: making scroll-revealed targets
    # REACHABLE must NOT mint coverage for a hover that does not exist. A target
    # resolved found:true but with NO event delta and NO :hover CSS rule must
    # still FAIL.
    entry = {"id": "deck", "selectors": [".resources_deck"], "channels": ["transform"]}
    verdict = evaluate_entry(entry, _sample(found=True))  # before == after, no css
    assert verdict["status"] == "fail", verdict


def test_css_hover_rule_static_verifies_unmounted_target() -> None:
    """Overlay-gated targets can't be event-probed, but the impl CSS carrying
    :hover rules for the declared channels is real compensating evidence."""
    entry = {"id": "x", "selectors": [".lightbox_btn"], "channels": ["color"]}
    verdict = evaluate_entry(
        entry, _sample(found=False, css_props=["background-color"])
    )
    assert verdict["status"] == "static-verified"


def test_unmounted_without_rules_fails() -> None:
    entry = {"id": "x", "selectors": [".lightbox_btn"], "channels": ["color"]}
    verdict = evaluate_entry(entry, _sample(found=False))
    assert verdict["status"] == "fail"


def test_color_delta_via_events_verified() -> None:
    entry = {"id": "x", "selectors": [".btn"], "channels": ["color"]}
    verdict = evaluate_entry(entry, _sample(bg_changed=True))
    assert verdict["status"] == "verified"


def test_missing_sample_is_unmeasured_fail() -> None:
    entry = {"id": "x", "selectors": [".btn"], "channels": ["color"]}
    verdict = evaluate_entry(entry, None)
    assert verdict["status"] == "fail"
    assert "unmeasured" in str(verdict["reason"])


# ── tools batch-6 ITEM 4: cascade-aware forced-hover end-state ──────────
# Attack 1 (evidence /tmp/adv-hover): a `:hover{width:auto}` rule exists (so the
# CSSOM walk records "width: auto") but a higher-priority `width:0 !important`
# base rule defeats it — the label NEVER expands. Rule presence is not proof;
# the gate must use the COMPUTED end-state under a REAL (forced) hover.


def _size_sample(*, width_before, forced_width, css_props=None, found=True):  # type: ignore[no-untyped-def]
    s = _sample(found=found, css_props=css_props or [], width_before=width_before,
                width_after=width_before)
    s["forcedHover"] = {"width": forced_width, "bg": "rgb(1,1,1)", "color": "c1",
                        "opacity": 1.0, "transform": "none"}
    return s


def test_cascade_neutralized_hover_rule_fails() -> None:
    # dead :hover{width:auto} present, but forced (real) hover leaves width at 0
    # — the base !important wins. Must FAIL, not static-verify on rule presence.
    entry = {"id": "x", "selectors": [".nav_label_container"], "channels": ["size"]}
    verdict = evaluate_entry(
        entry, _size_sample(width_before=0.0, forced_width=0.0, css_props=["width: auto"])
    )
    assert verdict["status"] == "fail", verdict


def test_forced_hover_expansion_verified() -> None:
    # the :hover rule actually wins the cascade — forced hover expands width.
    entry = {"id": "x", "selectors": [".nav_label_container"], "channels": ["size"]}
    verdict = evaluate_entry(
        entry, _size_sample(width_before=0.0, forced_width=92.0, css_props=["width: auto"])
    )
    assert verdict["status"] == "verified", verdict


def test_size_css_static_still_allowed_without_forced_measurement() -> None:
    # Legacy path: when no forced-hover measurement is available (older probe),
    # a covering :hover size rule on a mounted target still static-verifies — we
    # must not false-fail clones probed without forced-hover support.
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(entry, _sample(width_before=0.0, width_after=0.0,
                                            css_props=["width: auto"]))
    assert verdict["status"] == "static-verified", verdict


# ── batch-13 ITEM 5: honest skip for an OFF-SCREEN, un-probeable size spring ──
# The realfood ref-vs-ref case: a framer whileHover width:0->auto spring on a
# floating nav pill that is clipped off-screen (above the viewport) at every
# scroll position. A CDP hover cannot engage an off-screen trigger, so the
# non-expansion is INCONCLUSIVE — an honest documented skip, not a fail and not
# a forged pass. Presence is enforced against the impl bundle elsewhere.


def test_offscreen_size_spring_is_honest_skip_not_fail() -> None:
    entry = {"id": "size-expansion:label_container",
             "selectors": [".nav_label_container"], "channels": ["size"]}
    s = _size_sample(width_before=0.0, forced_width=0.0)  # forced ran, no growth
    s["offScreen"] = True  # trigger clipped outside the viewport
    verdict = evaluate_entry(entry, s)
    assert verdict["status"] == "skip", verdict
    assert "off-screen" in str(verdict["reason"]).lower()


def test_onscreen_size_no_expansion_still_fails() -> None:
    # Regression guard: an ON-screen target that does not expand under a forced
    # hover is PROVABLE absence and must still FAIL — the off-screen skip must not
    # leak to reachable targets.
    entry = {"id": "size-expansion:label_container",
             "selectors": [".nav_label_container"], "channels": ["size"]}
    s = _size_sample(width_before=0.0, forced_width=0.0)
    s["offScreen"] = False
    assert evaluate_entry(entry, s)["status"] == "fail", s


def test_offscreen_skip_does_not_apply_when_forced_grows() -> None:
    # An off-screen spring that DOES expand under the forced hover is real
    # evidence — verify it, never downgrade to skip.
    entry = {"id": "size-expansion:label_container",
             "selectors": [".nav_label_container"], "channels": ["size"]}
    s = _size_sample(width_before=0.0, forced_width=92.0)
    s["offScreen"] = True
    assert evaluate_entry(entry, s)["status"] == "verified", s


def test_offscreen_skip_scoped_to_size_only_channels() -> None:
    # A non-size channel (transform) that fails off-screen is NOT eligible for the
    # size-spring skip — it still fails (the skip is scoped to size-expansion
    # springs whose trigger is the unreachable nav pill).
    entry = {"id": "deck", "selectors": [".resources_deck"], "channels": ["transform"]}
    s = _sample(found=True)  # transform none->none, no delta
    s["offScreen"] = True
    assert evaluate_entry(entry, s)["status"] == "fail", s


def test_cli_verdict_writes_artifact(tmp_path: Path) -> None:
    import subprocess
    import sys

    ref = _ref(tmp_path)
    samples = {
        "hover-nav-buttons": _sample(bg_changed=True),
        "size-expansion:label_container": _sample(width_before=0.0, width_after=0.0),
    }
    samples_file = tmp_path / "samples.json"
    samples_file.write_text(json.dumps(samples))
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.gates.hover_probe",
         "verdict", str(ref), str(samples_file)],
        capture_output=True, text=True, timeout=60, cwd=str(root),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hover-fallback.json").read_text())
    assert art["status"] == "fail"
    by_id = {e["id"]: e for e in art["entries"]}
    assert by_id["hover-nav-buttons"]["status"] == "verified"
    assert by_id["size-expansion:label_container"]["status"] == "fail"


def test_dead_zero_width_hover_rule_does_not_static_verify() -> None:
    """Review-2 finding 3 (bypass attempt): an impl shipping
    ':hover { width: 0 }' carries the property NAME but the collapsed VALUE —
    a dead rule must not static-verify a size-expansion entry."""
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(
        entry,
        _sample(found=False, css_props=["width: 0px"]),
    )
    assert verdict["status"] == "fail", verdict


def test_expanding_hover_rule_value_static_verifies() -> None:
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    for value in ("width: auto", "max-width: 160px", "width: fit-content"):
        verdict = evaluate_entry(
            entry, _sample(found=False, css_props=[value])
        )
        assert verdict["status"] == "static-verified", (value, verdict)


def test_unparseable_size_value_requires_event_delta() -> None:
    """A size value the probe cannot prove expands (var(--x), calc of
    unknowns) is not static evidence — the entry needs a live delta."""
    entry = {"id": "x", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(
        entry, _sample(found=True, css_props=["width: var(--w)"],
                       width_before=0.0, width_after=0.0),
    )
    assert verdict["status"] == "fail", verdict


def test_legacy_name_only_props_still_handled() -> None:
    """Older samples carried bare property names; for non-size channels the
    name keeps counting (value comparison is not computable cross-format),
    and bare names on size channels are no longer sufficient."""
    color_entry = {"id": "c", "selectors": [".btn"], "channels": ["color"]}
    verdict = evaluate_entry(
        color_entry, _sample(found=False, css_props=["background-color"])
    )
    assert verdict["status"] == "static-verified"
    size_entry = {"id": "s", "selectors": [".label"], "channels": ["size"]}
    verdict = evaluate_entry(
        size_entry, _sample(found=False, css_props=["width"])
    )
    assert verdict["status"] == "fail", verdict


def test_partial_coverage_does_not_suppress_probe(tmp_path: Path) -> None:
    """Review-2 finding 2 (bypass attempt): one measured hover run must not
    suppress probing of the remaining entries — the unmeasured size-expansion
    entry still fails while the measured entry is marked covered."""
    import os
    import subprocess
    import sys

    ref = _ref(tmp_path)
    measured = tmp_path / "measured.txt"
    measured.write_text(".nav_dot_button\n", encoding="utf-8")
    samples = {
        # measured entry: no probe sample needed
        # expansion entry: probed, dead (no delta, no rules)
        "size-expansion:label_container": _sample(width_before=0.0, width_after=0.0),
    }
    samples_file = tmp_path / "samples.json"
    samples_file.write_text(json.dumps(samples))
    root = Path(__file__).resolve().parents[2]
    # a measured run IS a real scan — carry the provenance flag the live probe
    # sets (batch-6 ITEM 4) AND the receipt the probe writes (batch-7 ITEM 4b);
    # the measured shortcut only counts when scanned.
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    env = dict(os.environ, UI_CLONE_HOVER_MEASURED_FILE=str(measured),
               UI_CLONE_HOVER_RUNTIME_SCANNED="1",
               UI_CLONE_HOVER_SCAN_RECEIPT=str(receipt))
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.gates.hover_probe",
         "verdict", str(ref), str(samples_file)],
        capture_output=True, text=True, timeout=60, cwd=str(root), env=env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hover-fallback.json").read_text())
    by_id = {e["id"]: e for e in art["entries"]}
    assert by_id["hover-nav-buttons"]["status"] == "measured"
    assert by_id["size-expansion:label_container"]["status"] == "fail"


# ── tools batch-6 ITEM 4: provenance (Attacks 3a/3b) ────────────────────


def _verdict(ref: Path, samples: dict, tmp_path: Path, **env_over):  # type: ignore[no-untyped-def]
    import os
    import subprocess
    import sys

    samples_file = tmp_path / "samples.json"
    samples_file.write_text(json.dumps(samples))
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, **env_over)
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.gates.hover_probe",
         "verdict", str(ref), str(samples_file)],
        capture_output=True, text=True, timeout=60, cwd=str(root), env=env,
    )
    art = json.loads((ref / "hover-fallback.json").read_text())
    return proc, art


def test_unscanned_all_verified_pass_is_blocked(tmp_path: Path) -> None:
    # Attack 3a: fabricated samples (no browser ran) where every entry would
    # verify. Without a real runtime scan the pass must not stand.
    ref = _ref(tmp_path)
    samples = {
        "hover-nav-buttons": _sample(bg_changed=True),
        "size-expansion:label_container": _sample(width_before=0.0, width_after=90.0),
    }
    proc, art = _verdict(ref, samples, tmp_path)  # no UI_CLONE_HOVER_RUNTIME_SCANNED
    assert art["runtimeScanned"] is False, art
    assert art["status"] == "fail", art
    assert proc.returncode == 1


def test_scanned_all_verified_passes(tmp_path: Path) -> None:
    ref = _ref(tmp_path)
    samples = {
        "hover-nav-buttons": _sample(bg_changed=True),
        "size-expansion:label_container": _sample(width_before=0.0, width_after=90.0),
    }
    # runtimeScanned now requires BOTH the env flag AND an existing scan receipt
    # (batch-7 ITEM 4b) — the probe writes the receipt only when a live scan ran.
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    proc, art = _verdict(
        ref, samples, tmp_path,
        UI_CLONE_HOVER_RUNTIME_SCANNED="1",
        UI_CLONE_HOVER_SCAN_RECEIPT=str(receipt),
    )
    assert art["runtimeScanned"] is True, art
    assert art["status"] == "pass", art
    assert proc.returncode == 0


def test_env_flag_without_receipt_is_not_scanned(tmp_path: Path) -> None:
    # Attack 4 / 4d: the attacker sets UI_CLONE_HOVER_RUNTIME_SCANNED=1 with
    # fabricated samples but no probe ran, so no receipt file exists. The flag
    # alone no longer attests a scan — runtimeScanned stays false and the pass
    # is blocked.
    ref = _ref(tmp_path)
    samples = {
        "hover-nav-buttons": _sample(bg_changed=True),
        "size-expansion:label_container": _sample(width_before=0.0, width_after=90.0),
    }
    proc, art = _verdict(
        ref, samples, tmp_path,
        UI_CLONE_HOVER_RUNTIME_SCANNED="1",
        UI_CLONE_HOVER_SCAN_RECEIPT=str(tmp_path / "does-not-exist.json"),
    )
    assert art["runtimeScanned"] is False, art
    assert art["status"] == "fail", art
    assert proc.returncode == 1


def test_forged_measured_file_without_scan_does_not_mark_measured(tmp_path: Path) -> None:
    # Attack 3b: a forged measured-file lists selectors with no real run. Without
    # a runtime scan the measured shortcut must not grant the highest-trust
    # "measured" status.
    ref = _ref(tmp_path)
    measured = tmp_path / "measured.txt"
    measured.write_text(".nav_dot_button\n", encoding="utf-8")
    samples: dict = {}
    proc, art = _verdict(ref, samples, tmp_path, UI_CLONE_HOVER_MEASURED_FILE=str(measured))
    by_id = {e["id"]: e for e in art["entries"]}
    assert by_id["hover-nav-buttons"]["status"] != "measured", art


def test_hover_state_compare_probes_unconditionally() -> None:
    """Review-2 finding 2 lock: the fallback probe must not be gated on
    RUN_COUNT==0 — every run invokes it with the measured-selector file."""
    script = (
        Path(__file__).resolve().parents[2]
        / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    )
    text = script.read_text(encoding="utf-8")
    assert "UI_CLONE_HOVER_MEASURED_FILE" in text
    assert 'if [ "$RUN_COUNT" -eq 0 ]; then\n  # Review-1 honesty pattern' not in text
