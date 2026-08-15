"""Unit tests for the shared visible-identity + settle primitive.

ITEM 0 of the adversarial-hardening batch. This primitive is the single
"resolve the rendered-visible target" helper used by every probe gate
(masked-region-static, state-reveal, alignment-parity, hover, junk-token).
It closes four recurring bypass classes:

  A. identity-by-index/class-name lets a decoy absorb the comparison
  B. paint-blindness — geometry passes while text is transparent / font-size:0
  C. single-instant sampling — a defect that settles AFTER the probe passes
  (D is provenance, handled per-gate)

The JS collector (skills/visual-debug/scripts/lib/visible-identity.js) emits
rich per-element records; this Python mirror evaluates them with identical
thresholds. Tested in isolation here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ui_clone.gates.visible_identity as visible_identity
from ui_clone.gates.visible_identity import (
    BG_IMAGE_COVERAGE_FLOOR,
    DEFAULT_MARGIN_PX,
    MATERIAL_OCCLUSION,
    MIN_AREA_PX2,
    MIN_FONT_PX,
    OPAQUE_ALPHA,
    is_laid_out,
    is_on_screen,
    is_rendered,
    is_settled,
    is_visible,
    occluded_verdict,
    paints_content,
    paints_text,
    resolve_visible,
    settled_value,
)

_ = is_rendered  # re-exported truth predicate (used indirectly via is_visible)

VP = (1280, 800)


def _rec(**over: object) -> dict:
    """A fully-painted, on-screen, laid-out, content-bearing element."""
    base: dict = {
        "selector": ".target",
        "index": 0,
        "tag": "h2",
        "className": "target",
        "display": "block",
        "visibility": "visible",
        "opacity": 1.0,
        "rect": {"top": 100, "left": 200, "width": 400, "height": 40},
        "colorAlpha": 1.0,
        "fontSizePx": 18.0,
        "bgColorAlpha": 0.0,
        "hasBgImage": False,
        "hasText": True,
        "replaced": False,
        "clientWidth": 1280,
        "clientHeight": 800,
    }
    base.update(over)
    return base


# ── is_laid_out: display/visibility/opacity/area ───────────────────────


def test_baseline_record_is_laid_out() -> None:
    assert is_laid_out(_rec()) is True


def test_display_none_not_laid_out() -> None:
    assert is_laid_out(_rec(display="none")) is False


def test_visibility_hidden_not_laid_out() -> None:
    assert is_laid_out(_rec(visibility="hidden")) is False


def test_zero_opacity_not_laid_out() -> None:
    assert is_laid_out(_rec(opacity=0.0)) is False


def test_opacity_accepts_string() -> None:
    # computed style values arrive as strings from CSSOM
    assert is_laid_out(_rec(opacity="0")) is False
    assert is_laid_out(_rec(opacity="1")) is True


def test_subthreshold_area_not_laid_out() -> None:
    # a 1x1 px element is below MIN_AREA_PX2
    assert MIN_AREA_PX2 >= 4
    assert is_laid_out(_rec(rect={"top": 0, "left": 0, "width": 1, "height": 1})) is False


def test_zero_width_not_laid_out() -> None:
    # collapsed width:0 label (honest loop-9 defect)
    assert is_laid_out(_rec(rect={"top": 0, "left": 0, "width": 0, "height": 40})) is False


# ── is_on_screen: viewport intersection within margin ──────────────────


def test_onscreen_element_passes() -> None:
    assert is_on_screen(_rec(), viewport=VP) is True


def test_offscreen_left_decoy_fails() -> None:
    # left:-99999px decoy (state-reveal Attack 3)
    r = _rec(rect={"top": 100, "left": -99999, "width": 95, "height": 24})
    assert is_on_screen(r, viewport=VP) is False


def test_offscreen_right_beyond_viewport_fails() -> None:
    # left:1400 at vpW 1280 (alignment-parity Attack 1 phantom)
    r = _rec(rect={"top": 100, "left": 1400, "width": 200, "height": 24})
    assert is_on_screen(r, viewport=VP) is False


def test_onscreen_uses_record_viewport_when_arg_absent() -> None:
    r = _rec(rect={"top": 100, "left": 1400, "width": 200, "height": 24})
    assert is_on_screen(r) is False


def test_margin_is_configurable() -> None:
    r = _rec(rect={"top": 100, "left": 1290, "width": 50, "height": 24})
    assert is_on_screen(r, viewport=VP, margin=0) is False
    assert is_on_screen(r, viewport=VP, margin=DEFAULT_MARGIN_PX + 50) is True


# ── paints_text / paints_content: paint-blindness ──────────────────────


def test_normal_text_paints() -> None:
    assert paints_text(_rec()) is True


def test_transparent_text_does_not_paint() -> None:
    # color:transparent => colorAlpha 0 (state-reveal Attack 1)
    assert paints_text(_rec(colorAlpha=0.0)) is False


def test_font_size_zero_does_not_paint() -> None:
    # font-size:0 (state-reveal Attack 1b)
    assert paints_text(_rec(fontSizePx=0.0)) is False
    assert MIN_FONT_PX > 0


def test_transparent_spacer_is_not_content() -> None:
    # 4px transparent-bg opacity:1 spacer (alignment-parity Attack 4 decoy):
    # no text, no bg color, no bg image, not replaced => paints nothing
    spacer = _rec(
        tag="div",
        rect={"top": 0, "left": 0, "width": 400, "height": 4},
        hasText=False,
        colorAlpha=0.0,
        bgColorAlpha=0.0,
        hasBgImage=False,
        replaced=False,
    )
    assert paints_content(spacer) is False


def test_background_color_counts_as_content() -> None:
    swatch = _rec(tag="div", hasText=False, bgColorAlpha=1.0)
    assert paints_content(swatch) is True


def test_replaced_element_counts_as_content() -> None:
    img = _rec(tag="img", hasText=False, replaced=True)
    assert paints_content(img) is True


# ── is_visible: composition ────────────────────────────────────────────


def test_is_visible_true_for_painted_onscreen_laidout() -> None:
    assert is_visible(_rec(), viewport=VP) is True


def test_is_visible_false_for_offscreen_decoy() -> None:
    assert is_visible(_rec(rect={"top": 100, "left": -99999, "width": 95, "height": 24}), viewport=VP) is False


def test_is_visible_false_for_transparent_text() -> None:
    assert is_visible(_rec(colorAlpha=0.0), viewport=VP) is False


# ── resolve_visible: cardinality / ambiguous-fail ──────────────────────


def test_resolve_single_visible_match_ok() -> None:
    res = resolve_visible([_rec()], expected=1, viewport=VP)
    assert res.status == "ok"
    assert res.target is not None


def test_resolve_decoy_plus_real_picks_only_visible() -> None:
    # Attack A/C: a display:none decoy sibling + the real visible element.
    decoy = _rec(display="none", className="decoy")
    real = _rec(index=1, className="real")
    res = resolve_visible([decoy, real], expected=1, viewport=VP)
    assert res.status == "ok"
    assert res.target is real


def test_resolve_two_visible_matches_is_ambiguous_fail() -> None:
    # two genuinely-visible matches where one was expected => fail loud,
    # not a silent pick (decoy must not be able to hide a real element)
    res = resolve_visible([_rec(index=0), _rec(index=1)], expected=1, viewport=VP)
    assert res.status == "ambiguous"
    assert res.target is None


def test_resolve_no_visible_match_is_none() -> None:
    res = resolve_visible([_rec(display="none")], expected=1, viewport=VP)
    assert res.status == "none"
    assert res.target is None


def test_resolve_expected_two_accepts_two_visible() -> None:
    res = resolve_visible([_rec(index=0), _rec(index=1)], expected=2, viewport=VP)
    assert res.status == "ok"
    assert len(res.visible) == 2


# ── tools batch-7 ITEM 1: pixel-truth (close the imperceptibility CLASS) ──
# Round-2 panel proved the fixed-property collector admits elements that paint
# NOTHING to a human: clip-path, filter:opacity(0), content-visibility:hidden,
# cascaded ancestor opacity/overflow-clip, and color==background. Each is
# recreated here as a RECORD (the new collector fields) that must read invisible,
# with controls (below-fold/pointer-events-none/::before/honest) that stay
# visible. Fixtures: /tmp/adv2-visid.


def test_clip_path_fully_hidden_reads_invisible() -> None:
    # clip-path:inset(100%) — rect/opacity/color untouched but clipped to nothing.
    assert is_visible(_rec(clipFullyHidden=True), viewport=VP) is False


def test_filter_opacity_zero_reads_invisible() -> None:
    # filter:opacity(0) — computed opacity stays 1, the element is transparent.
    assert is_visible(_rec(filterOpacityZero=True), viewport=VP) is False


def test_check_visibility_false_reads_invisible() -> None:
    # Element.checkVisibility(...) == false covers cascaded ancestor opacity:0,
    # content-visibility:hidden, and ancestor visibility/display generically.
    assert is_visible(_rec(checkVisibility=False), viewport=VP) is False


def test_content_visibility_hidden_rejected_on_no_paint_path() -> None:
    # masked-region resolves with require_paint=False; checkVisibility=false must
    # still reject (the content-visibility:hidden decoy can't count for cardinality).
    assert is_visible(_rec(checkVisibility=False), viewport=VP, require_paint=False) is False


def test_ancestor_overflow_clipped_reads_invisible() -> None:
    # child positioned outside an overflow:hidden ancestor box — own rect on-screen.
    assert is_visible(_rec(ancestorClipped=True), viewport=VP) is False


def test_content_visibility_hidden_with_intrinsic_height_reads_invisible() -> None:
    # batch-8 ITEM 2: content-visibility:hidden holds the border-box open via
    # contain-intrinsic-size but skips ALL content rendering. checkVisibility(
    # contentVisibilityAuto) reports true for :hidden, so geometry alone passed.
    rec = _rec(checkVisibility=True, contentVisibilityHidden=True)
    assert is_visible(rec, viewport=VP) is False


def test_large_text_indent_under_overflow_clip_reads_invisible() -> None:
    # batch-8 ITEM 3: text-indent:-9999px + overflow:hidden — box paints, glyphs
    # shoved off the element's own clip so nothing readable lands in the rect.
    rec = _rec(checkVisibility=True, textIndentHidden=True)
    assert is_visible(rec, viewport=VP) is False


def test_hit_test_blocked_reads_invisible() -> None:
    # something opaque is the topmost painted node at the element's center.
    assert is_visible(_rec(hitTest="blocked"), viewport=VP) is False


def test_occluding_cover_and_canvas_both_read_invisible() -> None:
    # batch-8 ITEM 1: a z-index opaque cover and an opaque canvas over the text
    # used to ship hitTest="self" (the JS hitTestAt returned "self" whenever el
    # was anywhere in the stack); the fixed collector now emits "blocked" so the
    # masked-region-static AND state-reveal gates both reject them.
    for rec in (
        _rec(hitTest="blocked", color=[20, 20, 20], effectiveBgColor=[255, 255, 255],
             checkVisibility=True, clipFullyHidden=False, filterOpacityZero=False,
             ancestorClipped=False),  # masked_region_static occlusion
        _rec(hitTest="blocked", tag="h2"),  # state_reveal occlusion
    ):
        assert is_visible(rec, viewport=VP) is False


def test_white_on_white_does_not_paint_text() -> None:
    # color == effective cascaded background: an empty pill, contrast ~1.0.
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255])
    assert paints_text(rec) is False
    assert is_visible(rec, viewport=VP) is False


def test_rgb_list_and_tuple_inputs_are_python39_compatible() -> None:
    list_rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255])
    tuple_rec = _rec(color=(255, 255, 255), effectiveBgColor=(255, 255, 255))

    assert paints_text(list_rec) is False
    assert paints_text(tuple_rec) is False

    tree = ast.parse(Path(visible_identity.__file__).read_text(encoding="utf-8"))
    pep604_isinstance_args = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.BinOp)
        and isinstance(node.args[1].op, ast.BitOr)
    ]
    assert pep604_isinstance_args == []


def test_low_alpha_text_does_not_paint() -> None:
    # rgba(...,0.01): effectively invisible but > 0 defeated the old binary test.
    assert paints_text(_rec(colorAlpha=0.01)) is False


def test_font_at_floor_does_not_paint() -> None:
    # fontSizePx == MIN_FONT (4) is unreadable; the floor must be strict (>).
    assert paints_text(_rec(fontSizePx=MIN_FONT_PX)) is False
    assert paints_text(_rec(fontSizePx=MIN_FONT_PX + 1)) is True


# ── ITEM 1 controls: the new false-positive surface must NOT regress ──────


def test_honest_visible_record_with_truth_fields_stays_visible() -> None:
    rec = _rec(
        checkVisibility=True, hitTest="self", clipFullyHidden=False,
        filterOpacityZero=False, ancestorClipped=False,
        color=[17, 17, 17], effectiveBgColor=[255, 255, 255],
    )
    assert is_visible(rec, viewport=VP) is True
    assert paints_text(rec) is True


def test_pointer_events_none_hittest_null_stays_visible() -> None:
    # elementsFromPoint skips pointer-events:none; the collector emits hitTest
    # null there, which must NOT be treated as hidden (false-positive guard).
    assert is_visible(_rec(hitTest=None), viewport=VP) is True


def test_descendant_hittest_stays_visible() -> None:
    # a child text span is the topmost node at the center — still the element.
    assert is_visible(_rec(hitTest="descendant"), viewport=VP) is True


# ── batch-9 ITEM 1: multi-point, paint-aware occlusion verdict ───────────
# The DECISION (grid sampling + paint-awareness) is made in the JS collector
# and exercised by the node harness (tests/test_visible_identity_js.py). The
# python mirror owns the shared thresholds + the pure tally verdict, and the
# gate ROUTE (is_visible/resolve_visible) must reject a "blocked" verdict and
# accept a "self" verdict. Recreates /tmp/adv4-occlusion + /tmp/adv4-gate.


def test_occlusion_thresholds_mirror_js() -> None:
    assert OPAQUE_ALPHA == 0.5
    assert MATERIAL_OCCLUSION == 0.5


def test_occluded_verdict_pure_tally_matches_js() -> None:
    # null when nothing measurable, blocked at/above the material fraction,
    # self below it — identical to lib/visible-identity.js occludedVerdict.
    assert occluded_verdict(0, 0) is None
    assert occluded_verdict(0, 9) == "self"
    assert occluded_verdict(4, 9) == "self"  # 0.44 < 0.5
    assert occluded_verdict(5, 9) == "blocked"  # 0.55 >= 0.5
    assert occluded_verdict(15, 15) == "blocked"


def test_partial_occlusion_cheat_blocked_record_fails_gate_route() -> None:
    # /tmp/adv4-gate/impl-10-partial shipped hitTest="self" (centre clear, 99%
    # covered) and BYPASSED. The multi-point collector now emits "blocked"; the
    # gate route (resolve_visible) must find no rendered-visible target.
    rec = _rec(
        tag="div", hitTest="blocked",
        rect={"top": 40, "left": 40, "width": 600, "height": 60},
        color=[17, 17, 17], effectiveBgColor=[255, 255, 255],
    )
    assert is_visible(rec, viewport=VP) is False
    res = resolve_visible([rec], expected=1, viewport=VP)
    assert res.status == "none"


def test_translucent_and_transparent_overlay_self_record_passes_gate_route() -> None:
    # /tmp/adv4-occlusion/04 (transparent overlay) shipped hitTest="blocked" — a
    # FALSE-POSITIVE — and /09 (rgba(...,0.04) scrim) likewise. The paint-aware
    # collector now emits "self"; the honest text must read rendered-visible.
    for hint in ("04 transparent overlay", "09 translucent scrim"):
        rec = _rec(
            tag="div", hitTest="self", className=hint,
            color=[17, 17, 17], effectiveBgColor=[255, 255, 255],
        )
        assert is_visible(rec, viewport=VP) is True, hint
        res = resolve_visible([rec], expected=1, viewport=VP)
        assert res.status == "ok", hint


def test_content_visibility_unset_stays_visible() -> None:
    # batch-8 ITEM 2 control: content-visibility unset/auto (flag absent/False)
    # must NOT flag — only the literal computed value "hidden" hides.
    assert is_visible(_rec(contentVisibilityHidden=False), viewport=VP) is True


def test_modest_text_indent_stays_visible() -> None:
    # batch-8 ITEM 3 control: a real first-line indent (glyphs land in-box)
    # reports textIndentHidden False (collector only flags an off-box shove
    # under an overflow clip), so it must stay visible.
    assert is_visible(_rec(textIndentHidden=False), viewport=VP) is True


def test_pseudo_before_content_counts_as_paint() -> None:
    # ::before/::after content paints even when innerText (hasText) is empty.
    rec = _rec(hasText=False, pseudoHasContent=True)
    assert paints_content(rec) is True
    assert is_visible(rec, viewport=VP) is True


def test_legacy_record_without_truth_fields_unaffected() -> None:
    # records lacking the new fields keep the batch-6 behaviour (visible).
    assert is_visible(_rec(), viewport=VP) is True


def test_low_contrast_but_distinct_text_still_paints() -> None:
    # a faint-but-distinguishable grey on white must NOT be flagged invisible.
    assert paints_text(_rec(color=[120, 120, 120], effectiveBgColor=[255, 255, 255])) is True


def test_hero_text_over_painting_bg_image_skips_contrast() -> None:
    # batch-8 ITEM 5 honest guard: white text over a real opaque covering photo
    # (>=10% sampled opaque coverage under the text rect) paints over the box —
    # contrast is legitimately skipped and the overlay stays readable.
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgIsImage=True, effectiveBgImagePaints=True,
               bgImageOpaqueCoverage=0.8)
    assert paints_text(rec) is True
    assert is_visible(rec, viewport=VP) is True
    # alpha/font floors still apply over a painting image
    assert paints_text(_rec(color=[255, 255, 255], effectiveBgImagePaints=True,
                            bgImageOpaqueCoverage=0.8, colorAlpha=0.01)) is False


# ── batch-9 ITEM 5: region-level bg-image paint sampling for contrast skip ──
# Recreates /tmp/adv4-paint: effectiveBgImagePaints accepted ANY decoded image
# (area>1) so a mostly-transparent bg-image with one opaque pixel skipped
# contrast and an invisible white-on-white text passed. Contrast is now skipped
# only when the SAMPLED opaque coverage under the text rect clears the floor.


def test_bg_image_coverage_floor_mirrors_js() -> None:
    assert BG_IMAGE_COVERAGE_FLOOR == 0.1


def test_mostly_transparent_bg_image_does_not_skip_contrast() -> None:
    # one opaque corner pixel — coverage < 10% — must NOT skip contrast; the
    # white-on-white text underneath is caught (invisible).
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgIsImage=True, effectiveBgImagePaints=True,
               bgImageOpaqueCoverage=0.01)
    assert paints_text(rec) is False
    assert is_visible(rec, viewport=VP) is False


def test_bg_image_coverage_at_floor_skips_contrast() -> None:
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgImagePaints=True, bgImageOpaqueCoverage=0.1)
    assert paints_text(rec) is True


def test_bg_image_coverage_below_floor_runs_contrast() -> None:
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgImagePaints=True, bgImageOpaqueCoverage=0.09)
    assert paints_text(rec) is False


def test_paints_flag_without_coverage_runs_contrast() -> None:
    # legacy / undecoded record: paints flag set but no sampled coverage — the
    # field defaults to 0 so contrast runs (never auto-pass on weak evidence).
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgImagePaints=True)
    assert paints_text(rec) is False


def test_white_on_white_under_contentless_bg_image_does_not_paint() -> None:
    # batch-8 ITEM 5 cheat: a 1x1 transparent gif / same-colour gradient sets
    # effectiveBgIsImage but paints zero/identical pixels — the broad skip let
    # invisible white-on-white pass. With no paint evidence contrast must run.
    rec = _rec(color=[255, 255, 255], effectiveBgColor=[255, 255, 255],
               effectiveBgIsImage=True, effectiveBgImagePaints=False)
    assert paints_text(rec) is False
    assert is_visible(rec, viewport=VP) is False


# ── settle: single-instant sampling (Attack C/D) ───────────────────────


def test_settled_value_takes_final_stable_state() -> None:
    # center at the probe instant, flips to the real left-aligned defect later
    samples = ["center", "center", "left", "left"]
    assert settled_value(samples) == "left"


def test_is_settled_true_when_trailing_window_agrees() -> None:
    assert is_settled(["center", "left", "left"]) is True


def test_is_settled_false_when_still_changing() -> None:
    assert is_settled(["center", "center", "left"]) is False


def test_settled_value_single_sample() -> None:
    assert settled_value(["left"]) == "left"
