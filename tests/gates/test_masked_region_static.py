"""ui_clone.gates.masked_region_static — static-style parity for dynamic-masked
regions.

Loop-10/11 regression class: the eatReal "Eat Real" h2 lives under a
`dynamic:true` mask selector, so section-compare, video-motion, and
masked-region-motion all mask it out — none of them check STATIC style. The
impl rendered the h2 left-aligned (no `text-align`) while the ref centers it,
and every gate passed. This gate compares phase-free computed styles between
the extraction-time ref ground truth (dom-scaffold) and the live impl DOM, so
a static style defect under a mask can no longer hide.
"""

from __future__ import annotations

import inspect

import ui_clone.gates.masked_region_static as masked_region_static
from ui_clone.gates.masked_region_static import (
    DEFAULT_STYLE_PROPS,
    build_ref_viewport_visibility,
    evaluate,
    ref_entries_from_scaffold,
    resolve_scaffold,
    select_masked_selectors,
)


def test_shell_cli_avoids_python39_incompatible_runtime_union_checks() -> None:
    source = inspect.getsource(masked_region_static)
    assert "isinstance(v, int | float)" not in source

SPEC = {
    "transitions": [
        {"id": "hero-video", "target": ".hero video", "dynamic": True},
        {
            "id": "eatreal-food-carousel",
            "target": ".dga_cards__vXMHq, .dga_eatReal_content__x6v1A h2",
            "dynamic": True,
        },
        {"id": "static-thing", "target": ".not-masked", "dynamic": False},
    ]
}

# dom-scaffold-shaped ref tree: eatReal_content (text-align center) wrapping the
# "Eat Real" h2 (also center).
SCAFFOLD = {
    "tree": {
        "tag": "body",
        "class": "",
        "styles": {},
        "children": [
            {
                "tag": "div",
                "class": "dga_eatReal_content__x6v1A",
                "styles": {
                    "text-align": "center",
                    "justify-content": "center",
                    "align-items": "center",
                    "ff": '"Die Grotesk D", system-ui',
                    "fw": "400",
                    "color": "rgb(253, 251, 238)",
                },
                "children": [
                    {
                        "tag": "h2",
                        "class": "",
                        "styles": {
                            "text-align": "center",
                            "ff": '"Die Grotesk D", system-ui',
                            "fw": "700",
                            "color": "rgb(253, 251, 238)",
                        },
                        "text": "Eat Real",
                    }
                ],
            }
        ],
    }
}


def _entry(
    selector: str,
    index: int,
    styles: dict,
    *,
    tag: str = "h2",
    class_sig: str = "",
) -> dict:
    styles = dict(styles)
    # display is a top-level meta field on real entries (drives flex-only
    # applicability), not a compared style — mirror that here.
    display = styles.pop("display", "")
    return {
        "selector": selector,
        "index": index,
        "tag": tag,
        "classSig": class_sig,
        "display": display,
        "styles": styles,
    }


def test_select_masked_selectors_flattens_dynamic_targets() -> None:
    sels = select_masked_selectors(SPEC)
    assert ".dga_cards__vXMHq" in sels
    assert ".dga_eatReal_content__x6v1A h2" in sels
    assert ".hero video" in sels
    # non-dynamic entries must not contribute
    assert ".not-masked" not in sels


def test_resolve_scaffold_descendant_combinator_finds_h2() -> None:
    nodes = resolve_scaffold(SCAFFOLD["tree"], ".dga_eatReal_content__x6v1A h2")
    assert len(nodes) == 1
    assert nodes[0]["tag"] == "h2"
    assert nodes[0]["styles"]["text-align"] == "center"


def test_ref_entries_maps_scaffold_abbreviations() -> None:
    entries = ref_entries_from_scaffold(
        SCAFFOLD["tree"],
        [".dga_eatReal_content__x6v1A h2"],
        ["text-align", "font-family", "font-weight"],
    )
    assert len(entries) == 1
    styles = entries[0]["styles"]
    assert styles["text-align"] == "center"
    # "ff"/"fw" scaffold abbreviations map to full CSS prop names
    assert "Die Grotesk D" in styles["font-family"]
    assert styles["font-weight"] == "700"


def test_h2_text_align_mismatch_fails() -> None:
    ref = [_entry(".eatReal h2", 0, {"text-align": "center"})]
    impl = [_entry(".eatReal h2", 0, {"text-align": "start"})]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail"
    fails = [r for r in result["rows"] if r["status"] == "fail"]
    assert any(r["property"] == "text-align" for r in fails)


def test_ref_self_passes() -> None:
    ref = [_entry(".eatReal h2", 0, {"text-align": "center", "font-weight": "700"})]
    impl = [_entry(".eatReal h2", 0, {"text-align": "center", "font-weight": "700"})]
    result = evaluate(ref, impl, ["text-align", "font-weight"])
    assert result["status"] == "pass"
    assert all(r["status"] == "ok" for r in result["rows"])


def test_start_left_equivalence_does_not_fail() -> None:
    # CSS `start` == `left` for LTR — must not be a false positive.
    ref = [_entry(".x", 0, {"text-align": "left"})]
    impl = [_entry(".x", 0, {"text-align": "start"})]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "pass"


def test_missing_impl_element_fails_not_silent_pass() -> None:
    ref = [_entry(".cards", 0, {"text-align": "center"})]
    impl: list[dict] = []
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail"
    assert any(r.get("reason", "").startswith("impl element absent") for r in result["rows"])


def test_unresolved_selector_is_unmeasured_not_pass() -> None:
    # An entry whose selector matches nothing on either side is explicit debt.
    result = evaluate([], [], ["text-align"], requested_selectors=[".ghost"])
    assert result["status"] in ("warn", "skip")
    assert result["unmeasured"]


def test_default_props_are_viewport_independent() -> None:
    # font-size is responsive — it must not be a default (would break ref-self
    # when impl is probed at a different viewport than extraction).
    assert "text-align" in DEFAULT_STYLE_PROPS
    assert "font-size" not in DEFAULT_STYLE_PROPS


def test_attribute_selector_is_unmeasured_not_false_absent() -> None:
    # The scaffold cannot faithfully resolve `div[aria-live]` (it does not store
    # attributes), so matching it permissively (all divs) against the browser's
    # strict match produces spurious "element absent" rows — and breaks
    # ref-self. Such selectors must be reported unmeasured, never compared.
    from ui_clone.gates.masked_region_static import partition_selectors

    resolvable, unresolvable = partition_selectors(
        [".dga_eatReal_content__x6v1A div[aria-live]", ".dga_cards__vXMHq"]
    )
    assert ".dga_cards__vXMHq" in resolvable
    assert ".dga_eatReal_content__x6v1A div[aria-live]" in unresolvable


def test_justify_align_inert_on_non_flex_not_flagged() -> None:
    # justify-content/align-items only affect flex/grid containers. A block
    # element reporting justify-content:normal vs the ref's center is inert —
    # flagging it is noise. Only compare when the ref element is flex/grid.
    ref = [
        _entry(".x", 0, {"justify-content": "center", "display": "block"}, tag="div"),
    ]
    impl = [
        _entry(".x", 0, {"justify-content": "normal", "display": "block"}, tag="div"),
    ]
    result = evaluate(ref, impl, ["justify-content"])
    assert result["status"] != "fail"


def test_justify_align_flagged_on_flex() -> None:
    ref = [_entry(".x", 0, {"justify-content": "center", "display": "flex"}, tag="div")]
    impl = [_entry(".x", 0, {"justify-content": "normal", "display": "flex"}, tag="div")]
    result = evaluate(ref, impl, ["justify-content"])
    assert result["status"] == "fail"


# ── tools batch-6 ITEM 1: decoy-resolution + settle bypasses ──────────
# Each fixture recreates an attacker DOM from /tmp/adv-mrs (the live evidence
# in wkqxulfz6.output). The OLD gate paired ref<->impl by (selector,index) and
# read styles once, so a decoy element matching the masked selector absorbed
# the comparison while the real defective element was never measured. The
# hardened gate resolves the rendered-VISIBLE match and uses the SETTLED style.

SEL = ".dga_eatReal_content h2"


def _rich(
    selector: str,
    index: int,
    styles: dict,
    *,
    tag: str = "h2",
    display: str = "block",
    visibility: str = "visible",
    opacity: float = 1.0,
    rect: dict | None = None,
    styles_samples: list[dict] | None = None,
) -> dict:
    """Impl entry shaped like the visible-identity collector output."""
    entry: dict = {
        "selector": selector,
        "index": index,
        "tag": tag,
        "classSig": "",
        "display": display,
        "visibility": visibility,
        "opacity": opacity,
        "rect": rect if rect is not None else {"top": 200, "left": 300, "width": 420, "height": 48},
        "colorAlpha": 1.0,
        "fontSizePx": 32.0,
        "bgColorAlpha": 0.0,
        "hasBgImage": False,
        "hasText": True,
        "replaced": False,
        "clientWidth": 1280,
        "clientHeight": 800,
        "styles": dict(styles),
    }
    if styles_samples is not None:
        entry["stylesSamples"] = styles_samples
    return entry


def test_attack_a_display_none_decoy_does_not_absorb_real_defect() -> None:
    # Attack A: a display:none decoy h2 (text-align:center) precedes the real
    # visible h2 (text-align:left). Both match the masked selector; ref has only
    # index 0. The OLD gate compared ref[0] against the decoy at impl index 0
    # and passed. The visible h2 (the real defect) must be the one measured.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(SEL, 0, {"text-align": "center"}, display="none"),
        _rich(SEL, 1, {"text-align": "left"}),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result
    fails = [r for r in result["rows"] if r["status"] == "fail"]
    assert any(r.get("property") == "text-align" and r.get("implValue") == "left" for r in fails), result["rows"]


def test_attack_c_offscreen_decoy_only_is_unresolved_fail() -> None:
    # Attack C: the masked selector matches ONLY an off-screen clipped decoy
    # (text-align:center); the real visible heading renders under a renamed
    # class the selector never matches. The decoy is not rendered-visible, so
    # there is no measurable visible target — must fail, not pass on the decoy.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(SEL, 0, {"text-align": "center"}, rect={"top": 200, "left": -99999, "width": 420, "height": 48}),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result


def test_two_visible_matches_is_ambiguous_fail() -> None:
    # A decoy that is genuinely on-screen alongside the real element makes the
    # visible cardinality exceed the ref's expectation — ambiguous, fail loud
    # rather than silently picking one.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(SEL, 0, {"text-align": "center"}),
        _rich(SEL, 1, {"text-align": "left"}),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result
    assert any("ambiguous" in (r.get("reason") or "").lower() for r in result["rows"]), result["rows"]


def test_attack_d_settle_uses_post_window_state() -> None:
    # Attack D: text-align renders center at the probe instant then flips to the
    # real broken left-aligned state after the fixed wait. The settled (final)
    # style is the defect the user is left with — it must be measured.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(
            SEL, 0, {"text-align": "center"},
            styles_samples=[{"text-align": "center"}, {"text-align": "left"}],
        ),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result


def test_settled_stable_correct_state_passes() -> None:
    # Control: a faithful impl whose style is center at every sample passes —
    # the settle defence must not false-fail a stable correct state.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(
            SEL, 0, {"text-align": "center"},
            styles_samples=[{"text-align": "center"}, {"text-align": "center"}],
        ),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "pass", result


def test_visible_real_clone_ref_self_passes() -> None:
    # ref-self / faithful clone with rich visible records: a single on-screen
    # painted h2 carrying the ref's style passes.
    ref = [_entry(SEL, 0, {"text-align": "center", "font-weight": "700"})]
    impl = [_rich(SEL, 0, {"text-align": "center", "font-weight": "700"})]
    result = evaluate(ref, impl, ["text-align", "font-weight"])
    assert result["status"] == "pass", result


# ── batch-10 ITEM 1: painted-glyph-extent occlusion at the gate route ──
# The R5 F2 regression: a short centered masked heading in a wide block, flanked
# by opaque decorative rules over the EMPTY box area, read hitTest="blocked"
# under the bounding-box grid and masked_region_static failed it "impl element
# absent" — though the glyphs are fully readable. The glyph-extent sampler
# (lib/visible-identity.js, node-tested) now emits "self" for that record, so
# the gate must read it present/comparable. A genuinely occluded label
# (hitTest="blocked") must still read absent.


def test_glyph_extent_self_heading_is_present_not_absent() -> None:
    # F2-shaped record: the painted-glyph-extent sampler emits hitTest="self"
    # for the readable centered heading. The masked gate must compare it, not
    # report "impl element absent".
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [_rich(SEL, 0, {"text-align": "center"})]
    impl[0]["hitTest"] = "self"
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "pass", result
    assert not any(
        r.get("reason", "").startswith("impl element absent") for r in result["rows"]
    ), result["rows"]


def test_occluded_label_blocked_record_reads_absent() -> None:
    # occluded-label control: a heading fully covered by an opaque panel emits
    # hitTest="blocked"; is_rendered rejects it, so the masked gate reads it
    # absent (the occlusion bypass detection is NOT loosened by ITEM 1).
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [_rich(SEL, 0, {"text-align": "center"})]
    impl[0]["hitTest"] = "blocked"
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result
    assert any(
        r.get("reason", "").startswith("impl element absent") for r in result["rows"]
    ), result["rows"]


# ── batch-8 ITEM 9/10: honest false-positives the per-viewport/on-screen
# gates produced — these HONEST clones must now PASS, while the decoy and
# genuine-absence controls must still FAIL. ──


def test_responsive_hidden_passes_only_when_ref_also_hidden() -> None:
    # batch-9 ITEM 2: a masked selector visible at desktop but display:none at a
    # narrow @media breakpoint is legitimately responsive-hidden ONLY when the
    # REF is also hidden there. The per-viewport expectation now comes from the
    # ref (ref_hidden_viewports), not the impl's own visible-elsewhere set.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    desktop = _rich(SEL, 0, {"text-align": "center"})  # clientWidth 1280, visible
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 390
    result = evaluate(ref, [desktop, mobile], ["text-align"],
                      ref_hidden_viewports={SEL: [390]})
    assert result["status"] == "pass", result
    assert any(u.get("viewport") == 390 for u in result["unmeasured"]), result


def test_respbypass_impl_hides_where_ref_shows_fails() -> None:
    # batch-9 ITEM 2 BYPASS (/tmp/adv4-respbypass): the impl @media-hides a
    # heading the ref shows at mobile. The old impl-derived excuse recorded the
    # 390 bucket "responsive-hidden" and PASSED. With no ref evidence the heading
    # is hidden at 390 (fail-closed), the missing mobile heading must now FAIL.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    desktop = _rich(SEL, 0, {"text-align": "center"})  # 1280, visible
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 390
    result = evaluate(ref, [desktop, mobile], ["text-align"])  # no ref hidden data
    assert result["status"] == "fail", result
    assert any(
        r.get("viewport") == 390
        and r["status"] == "fail"
        and r.get("reason", "").startswith("impl element absent")
        for r in result["rows"]
    ), result


def test_below_fold_masked_element_measured_not_absent() -> None:
    # ITEM 10 + batch-9 minor: a faithful below-fold masked heading is measured
    # ONLY with a post-scroll viewport-intersection proof (scrolledIntoView). The
    # probe scrolls it into view and stamps the field; the below-fold tolerance
    # then applies.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    rich = _rich(SEL, 0, {"text-align": "center"},
                 rect={"top": 1920, "left": 24, "width": 1232, "height": 55})
    rich["scrolledIntoView"] = True
    result = evaluate(ref, [rich], ["text-align"])
    assert result["status"] == "pass", result


def test_below_fold_without_scroll_proof_reads_absent() -> None:
    # batch-9 minor (/tmp/adv4-belowfold-unreach): a below-fold element with NO
    # post-scroll intersection proof was never scrolled into view — the
    # below-fold tolerance must NOT apply, so an unreachable decoy below the fold
    # cannot ride the exemption. It reads off-screen => absent => fail.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    rich = _rich(SEL, 0, {"text-align": "center"},
                 rect={"top": 1920, "left": 24, "width": 1232, "height": 55})
    assert evaluate(ref, [rich], ["text-align"])["status"] == "fail"


def test_below_fold_tolerance_keeps_x_offscreen_decoy_failing() -> None:
    # control: below_fold_ok relaxes ONLY the y-axis below-fold; a horizontal
    # off-screen decoy (Attack C) and an above-viewport element are still
    # rejected, so an off-screen clone cannot ride the below-fold exemption.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    x_off = [_rich(SEL, 0, {"text-align": "center"},
                   rect={"top": 200, "left": -99999, "width": 420, "height": 48})]
    assert evaluate(ref, x_off, ["text-align"])["status"] == "fail"
    above = [_rich(SEL, 0, {"text-align": "center"},
                   rect={"top": -9999, "left": 24, "width": 420, "height": 48})]
    assert evaluate(ref, above, ["text-align"])["status"] == "fail"


def test_genuinely_absent_everywhere_still_fails() -> None:
    # control: a masked selector with NO rendered-visible match and NO ref
    # evidence it is responsive-hidden anywhere is genuinely absent — fail-closed.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [_rich(SEL, 0, {"text-align": "center"}, display="none",
                  rect={"top": 0, "left": 0, "width": 0, "height": 0})]
    assert evaluate(ref, impl, ["text-align"])["status"] == "fail"


# ── batch-10 ITEM 4: per-viewport REF COVERAGE (unmeasured vs absent) ──
# ref-viewport-visibility.json is optional; when the ref capture did NOT include
# a probed viewport, the gate defaulted that viewport to ref_expects_here=True
# and false-failed an honest responsive clone whose ref simply wasn't captured
# there. A viewport with NO ref-side evidence (not in ref_measured_viewports /
# the JSON's capturedViewports) is now UNMEASURED, never absent. The fail-closed
# default is preserved where the ref positively shows the selector at a captured
# viewport (the respbypass anti-cheat) and when no coverage is provided at all.


def test_uncaptured_viewport_is_unmeasured_not_absent() -> None:
    # ref captured only at 1280; impl probed at 375 (responsively hidden). 375 is
    # not in the ref's captured viewports => no evidence the ref shows it there =>
    # UNMEASURED, not an "impl element absent" fail.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 375
    result = evaluate(ref, [mobile], ["text-align"], ref_measured_viewports=[1280])
    assert result["status"] != "fail", result
    assert any(
        u.get("viewport") == 375 and "not captured" in u.get("reason", "")
        for u in result["unmeasured"]
    ), result


def test_captured_viewport_where_ref_shows_still_fails_absent() -> None:
    # WITH coverage data: 390 is a captured ref viewport where the selector is not
    # recorded hidden => the ref shows it there => a 0-visible impl bucket is an
    # absent defect (the respbypass anti-cheat is preserved under coverage).
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    desktop = _rich(SEL, 0, {"text-align": "center"})  # 1280 visible
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 390
    result = evaluate(ref, [desktop, mobile], ["text-align"],
                      ref_measured_viewports=[1280, 390])
    assert result["status"] == "fail", result
    assert any(
        r.get("viewport") == 390 and r["status"] == "fail"
        and r.get("reason", "").startswith("impl element absent")
        for r in result["rows"]
    ), result


def test_uncaptured_viewport_visible_impl_still_style_compared() -> None:
    # UNMEASURED relaxes ONLY the absent case: a VISIBLE impl at an uncaptured
    # viewport is still compared against the (viewport-independent) ref styles, so
    # a real text-align defect at 375 is not masked by the missing coverage.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    narrow = _rich(SEL, 0, {"text-align": "left"})
    narrow["clientWidth"] = 375
    result = evaluate(ref, [narrow], ["text-align"], ref_measured_viewports=[1280])
    assert result["status"] == "fail", result
    assert any(
        r.get("implValue") == "left" and r.get("viewport") == 375
        for r in result["rows"]
        if r["status"] == "fail"
    ), result


def test_no_coverage_data_stays_fail_closed() -> None:
    # back-compat: with NO ref coverage data the gate keeps the fail-closed
    # default — a 0-visible impl bucket at a viewport the ref is not recorded
    # hiding is an absent defect (the respbypass behaviour is unchanged).
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 390
    assert evaluate(ref, [mobile], ["text-align"])["status"] == "fail"


# ── tools batch-7 ITEM 2: true settle quiescence ──────────────────────
# The probe now samples until quiescence past a wall-clock floor and records the
# FULL ordered series; the verdict takes the settled (final) STATE and fails a
# series that never stabilised. Recreates /tmp/adv2-mrs / settle.html late-flip.


def test_late_flip_past_window_uses_settled_state() -> None:
    # text-align center through the old 2-sample window, flips to the real
    # broken left state past the settle floor — the quiescence series captures
    # it and the settled value (left) is measured.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(
            SEL, 0, {"text-align": "center"},
            styles_samples=[{"text-align": "center"}, {"text-align": "center"}, {"text-align": "left"}],
        ),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result


def test_quiescent_correct_series_passes() -> None:
    # three stable correct frames => quiescence reached, settled center == ref.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(SEL, 0, {"text-align": "center"},
              styles_samples=[{"text-align": "center"}] * 3),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "pass", result


def test_media_query_viewport_gated_text_align_fails() -> None:
    # batch-7 ITEM 3: text-align is viewport-dependent via @media. The probe now
    # samples at every fan-out viewport; the narrow bucket (left) fails vs the
    # ref center even though the wide bucket (center) matches.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    wide = _rich(SEL, 0, {"text-align": "center"})  # clientWidth 1280
    narrow = _rich(SEL, 0, {"text-align": "left"})
    narrow["clientWidth"] = 800
    result = evaluate(ref, [wide, narrow], ["text-align"])
    assert result["status"] == "fail", result
    fails = [r for r in result["rows"] if r["status"] == "fail"]
    assert any(r.get("implValue") == "left" and r.get("viewport") == 800 for r in fails), result["rows"]


def test_media_query_all_viewports_center_passes() -> None:
    # control: a faithful clone centered at every viewport passes.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    wide = _rich(SEL, 0, {"text-align": "center"})
    narrow = _rich(SEL, 0, {"text-align": "center"})
    narrow["clientWidth"] = 800
    assert evaluate(ref, [wide, narrow], ["text-align"])["status"] == "pass"


def test_oscillating_never_quiescent_series_fails() -> None:
    # a series that never stabilises (oscillates) cannot be certified settled,
    # even if the last sample happens to match the ref.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl = [
        _rich(
            SEL, 0, {"text-align": "center"},
            styles_samples=[{"text-align": "center"}, {"text-align": "left"}, {"text-align": "center"}],
        ),
    ]
    result = evaluate(ref, impl, ["text-align"])
    assert result["status"] == "fail", result
    assert any("still changing" in (r.get("reason") or "") for r in result["rows"]), result["rows"]


# ── tools-batch-11 ITEM 1: ref-viewport-visibility.json PRODUCER ──
# The verdict has consumed ref-viewport-visibility.json since batch-9/10, but
# NOTHING in the pipeline produced it, so the gate stayed permanently fail-closed
# (a ref that responsively/scroll-hides a masked selector at a probed viewport
# false-failed against its OWN reference — 24 "impl element absent" rows in
# loop-e2e-12, proven via ref-vs-ref). build_ref_viewport_visibility mirrors the
# impl probe against the LIVE REF and records, per masked selector, the captured
# viewport widths at which the ref renders 0 visible matches — using the SAME
# _impl_visible criterion the verdict applies to the impl. This makes the gate
# ref-vs-ref self-pass WITHOUT weakening the respbypass anti-cheat: the hidden
# set is derived only from the ref, so an impl that hides what the ref SHOWS
# still fails "impl element absent".


def test_ref_visibility_records_responsive_hidden_viewport() -> None:
    # The ref shows the masked selector at 1280 but renders 0 matches at 375
    # (responsive @media-hidden). The producer records 375 as hidden for the
    # selector and both widths as captured.
    desktop = _rich(SEL, 0, {"text-align": "center"})  # clientWidth 1280, visible
    out = build_ref_viewport_visibility([desktop], [SEL], [375, 1280])
    assert out["capturedViewports"] == [375, 1280]
    assert out["hiddenViewports"] == {SEL: [375]}


def test_ref_visibility_offscreen_unscrolled_is_hidden() -> None:
    # A ref record that is horizontally off-screen with no scroll-into-view proof
    # is not rendered-visible (_impl_visible False) → hidden at that viewport.
    off = _rich(SEL, 0, {"text-align": "center"},
                rect={"top": 200, "left": -99999, "width": 420, "height": 48})
    out = build_ref_viewport_visibility([off], [SEL], [1280])
    assert out["hiddenViewports"] == {SEL: [1280]}


def test_ref_visibility_below_fold_scrolled_into_view_is_visible() -> None:
    # A faithful below-fold ref element the probe scrolled into view (stamped
    # scrolledIntoView) IS rendered-visible → NOT hidden, so the verdict still
    # expects the impl to render it there.
    rich = _rich(SEL, 0, {"text-align": "center"},
                 rect={"top": 1920, "left": 24, "width": 1232, "height": 55})
    rich["scrolledIntoView"] = True
    out = build_ref_viewport_visibility([rich], [SEL], [1280])
    assert out["hiddenViewports"] == {}


def test_ref_visibility_visible_everywhere_has_no_hidden() -> None:
    wide = _rich(SEL, 0, {"text-align": "center"})  # 1280
    narrow = _rich(SEL, 0, {"text-align": "center"})
    narrow["clientWidth"] = 375
    out = build_ref_viewport_visibility([wide, narrow], [SEL], [375, 1280])
    assert out["hiddenViewports"] == {}
    assert out["capturedViewports"] == [375, 1280]


def test_ref_visibility_drives_ref_vs_ref_self_pass() -> None:
    # KEYSTONE: the ref hides SEL at 375 and shows it at 1280. Probing the LIVE
    # REF as the impl yields the SAME 0-visible bucket at 375. Feeding the
    # producer's output into the verdict excuses 375 (responsive-hidden in the
    # ref) instead of false-failing "impl element absent" → ref-vs-ref PASS.
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    desktop = _rich(SEL, 0, {"text-align": "center"})  # 1280 visible
    mobile = _rich(SEL, 0, {"text-align": "center"}, display="none",
                   rect={"top": 0, "left": 0, "width": 0, "height": 0})
    mobile["clientWidth"] = 375
    ref_probe = [desktop, mobile]  # live ref probed as impl
    vis = build_ref_viewport_visibility(ref_probe, [SEL], [375, 1280])
    result = evaluate(
        ref, ref_probe, ["text-align"],
        ref_hidden_viewports=vis["hiddenViewports"],
        ref_measured_viewports=vis["capturedViewports"],
    )
    assert result["status"] == "pass", result
    assert not any(
        (r.get("reason") or "").startswith("impl element absent")
        for r in result["rows"]
    ), result


def test_ref_visibility_does_not_excuse_real_respbypass_defect() -> None:
    # respbypass anti-cheat preserved: the REF SHOWS SEL at 375 (visible there),
    # so the producer does NOT mark 375 hidden. An impl that @media-hides the
    # heading at 375 must still FAIL "impl element absent" — the producer cannot
    # manufacture a false responsive excuse from impl-side hiding.
    ref_probe = [_rich(SEL, 0, {"text-align": "center"}, rect={"top": 200, "left": 24,
                       "width": 420, "height": 48})]
    ref_probe[0]["clientWidth"] = 375
    vis = build_ref_viewport_visibility(ref_probe, [SEL], [375])
    assert vis["hiddenViewports"] == {}  # ref shows it at 375
    ref = [_entry(SEL, 0, {"text-align": "center"})]
    impl_hides = [_rich(SEL, 0, {"text-align": "center"}, display="none",
                        rect={"top": 0, "left": 0, "width": 0, "height": 0})]
    impl_hides[0]["clientWidth"] = 375
    result = evaluate(
        ref, impl_hides, ["text-align"],
        ref_hidden_viewports=vis["hiddenViewports"],
        ref_measured_viewports=vis["capturedViewports"],
    )
    assert result["status"] == "fail", result
    assert any(
        r.get("viewport") == 375 and r["status"] == "fail"
        and (r.get("reason") or "").startswith("impl element absent")
        for r in result["rows"]
    ), result
