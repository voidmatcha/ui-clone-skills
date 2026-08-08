"""ui_clone.gates.state_reveal — state-driven reveal end-state proof.

Loop-10/11: scrolling swaps the nav active state but the newly-active button's
label never reveals (container baked width:0). The hover-fallback gate only
covers hover-triggered reveals; this gate drives the active-state change and
asserts the bundle-declared reveal (width 0 -> auto on the active flag) actually
occurs on the live impl.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.gates.state_reveal import (
    build_plan,
    evaluate,
    main,
    min_content_px,
    reveal_ratio,
    select_active_expansions,
)

_DEFECT_OBS = {
    "selector": ".nav_pill_label", "pct": 50, "text": "FAQs",
    "box": 8.0, "content": 74.0, "colorAlpha": 1.0, "opacity": 1.0,
    "fontSizePx": 16.0, "onScreen": True, "hasText": True,
}
_DEFECT_PLAN_EXP = [{"selector": ".nav_pill_label", "property": "width", "from": "0", "to": "auto"}]

EXTRACTION = {
    "extractions": {
        "activeStateExpansions": [
            {
                "kind": "active-state-expansion",
                "classToken": "label_container",
                "resolvedClassName": "nav_label_container__okVKb",
                "property": "width",
                "stateFlag": "a",
                "from": "0",
                "to": "auto",
            }
        ]
    }
}

PLAN = {
    "expansions": [
        {"selector": ".nav_label_container__okVKb", "property": "width", "from": "0", "to": "auto"}
    ],
    "revealRatio": 0.5,
    "minContentPx": 12.0,
}


def _obs(pct, text, box, content, selector=".nav_label_container__okVKb"):  # type: ignore[no-untyped-def]
    return {"selector": selector, "pct": pct, "text": text, "box": box, "content": content}


def test_select_active_expansions_uses_resolved_class() -> None:
    sels = select_active_expansions(EXTRACTION)
    assert sels == [
        {
            "selector": ".nav_label_container__okVKb",
            "property": "width",
            "from": "0",
            "to": "auto",
            "stateFlag": "a",
        }
    ]


def test_active_label_stays_collapsed_fails() -> None:
    # loop-11 impl: initial active reveals (Real Food box≈content) but on scroll
    # the newly-active FAQs label stays collapsed (box 8 vs content 74).
    obs = [_obs(0, "Real Food", 74.0, 74.0), _obs(60, "FAQs", 8.0, 74.0)]
    result = evaluate(PLAN, obs)
    assert result["status"] == "fail"
    bad = [r for r in result["rows"] if r["status"] == "fail"]
    assert bad and bad[0]["activeText"] == "FAQs"


def test_active_label_reveals_passes() -> None:
    obs = [_obs(0, "Real Food", 74.0, 74.0), _obs(60, "FAQs", 70.0, 74.0)]
    assert evaluate(PLAN, obs)["status"] == "pass"


def test_section_transition_collapse_tolerated_when_same_label_reveals_elsewhere() -> None:
    # batch-12 ITEM 6: the SAME label (selector + text "New Pyramid") collapses at a
    # section-transition scroll position (38%) but reveals at the position where its
    # section is stably active (50%). The transition collapse is tolerated; the gate
    # passes. (realfood ref-vs-ref: the probe sampled a section boundary.)
    obs = [
        _obs(38, "New Pyramid", 0.0, 98.0),
        _obs(50, "New Pyramid", 98.0, 98.0),
        _obs(63, "Real Food", 78.0, 78.0),
    ]
    assert evaluate(PLAN, obs)["status"] == "pass"


def test_label_that_never_reveals_still_fails_despite_others_revealing() -> None:
    # Detection guard: the transition tolerance is keyed on (selector, TEXT), so a
    # DIFFERENT label that NEVER reveals at any sampled position (the loop-11 "FAQs"
    # baked-collapsed defect) is NOT excused by other labels revealing.
    obs = [
        _obs(0, "Real Food", 74.0, 74.0),
        _obs(30, "Pyramid", 70.0, 74.0),
        _obs(60, "FAQs", 0.0, 74.0),
    ]
    res = evaluate(PLAN, obs)
    assert res["status"] == "fail"
    assert any(
        r["status"] == "fail" and r.get("activeText") == "FAQs" for r in res["rows"]
    ), res["rows"]


def test_empty_content_labels_are_unmeasured() -> None:
    # Icon-only / empty labels (content below the min) carry no text to reveal.
    result = evaluate(PLAN, [_obs(0, "", 0.0, 2.0)])
    assert result["status"] == "warn"
    assert result["unmeasured"]


def test_no_active_observations_is_unmeasured() -> None:
    result = evaluate(PLAN, [])
    assert result["status"] == "warn"


def test_no_declared_expansion_skips() -> None:
    # No activeStateExpansions => nothing to prove (a site with no active-state
    # reveal must not be force-failed).
    result = evaluate({"expansions": [], "revealRatio": 0.5, "minContentPx": 12.0}, [])
    assert result["status"] == "skip"


# ── per-selector aggregation (batch-4 review MAJOR 3) ────────────────────────
#
# The gate passed when ANY one measured active label revealed; a SUBSET of the
# declared activeStateExpansions firing let the rest vacuously pass. Aggregate
# per declared selector: every declared selector needs a measurable passing
# observation, else it is unmeasured/fail per the honesty convention.

PLAN2 = {
    "expansions": [
        {"selector": ".navA", "property": "width", "from": "0", "to": "auto"},
        {"selector": ".navB", "property": "width", "from": "0", "to": "auto"},
    ],
    "revealRatio": 0.5,
    "minContentPx": 12.0,
}


def _o(selector, box, content, text="x", pct=0):  # type: ignore[no-untyped-def]
    return {"selector": selector, "pct": pct, "text": text, "box": box, "content": content}


def test_subset_of_declared_selectors_revealing_does_not_pass() -> None:
    # only .navA observed + revealed; .navB never produced an observation.
    result = evaluate(PLAN2, [_o(".navA", 70.0, 74.0)])
    assert result["status"] != "pass", "a subset firing must not vacuously pass the rest"
    assert any(u.get("selector") == ".navB" for u in result["unmeasured"])


def test_all_declared_selectors_revealing_passes() -> None:
    obs = [_o(".navA", 70.0, 74.0), _o(".navB", 70.0, 74.0)]
    assert evaluate(PLAN2, obs)["status"] == "pass"


def test_any_declared_selector_failing_fails() -> None:
    obs = [_o(".navA", 70.0, 74.0), _o(".navB", 8.0, 74.0)]
    assert evaluate(PLAN2, obs)["status"] == "fail"


# ── tools batch-6 ITEM 2: paint-blindness + off-screen decoy bypasses ──
# Each fixture recreates an attacker observation from /tmp/adv-state-reveal.
# The OLD gate measured only box/content geometry and trusted the probe's
# isActive() pick, so a label could occupy full layout width while painting no
# readable text, or an off-screen decoy could absorb the measurement.


def _paint_obs(  # type: ignore[no-untyped-def]
    pct, text, box, content, *,
    selector=".nav_label_container__okVKb",
    color_alpha=1.0,
    opacity=1.0,
    font_px=14.0,
    on_screen=True,
    has_text=True,
    hit_test=None,
):
    obs = {
        "selector": selector, "pct": pct, "text": text, "box": box, "content": content,
        "colorAlpha": color_alpha, "opacity": opacity, "fontSizePx": font_px,
        "onScreen": on_screen, "hasText": has_text,
    }
    if hit_test is not None:
        obs["hitTest"] = hit_test
    return obs


def test_attack1_transparent_text_fails() -> None:
    # Attack 1: expand the active label to full content width but paint the text
    # color:transparent (box == scrollWidth, ratio 1.0) — an empty pill.
    obs = [_paint_obs(0, "New Pyramid", 79.0, 79.0, color_alpha=0.0)]
    result = evaluate(PLAN, obs)
    assert result["status"] == "fail", result
    bad = [r for r in result["rows"] if r["status"] == "fail"]
    assert bad and "paints no visible text" in (bad[0].get("reason") or ""), result["rows"]


def test_attack1b_font_size_zero_fails_not_warn() -> None:
    # Attack 1b: font-size:0 collapses scrollWidth below the min-content floor,
    # routing the label into the honest-unmeasurable warn branch. With paint
    # info the gate sees text rendered at font-size:0 and FAILS it.
    obs = [_paint_obs(0, "New Pyramid", 0.0, 2.0, font_px=0.0)]
    result = evaluate(PLAN, obs)
    assert result["status"] == "fail", result


def test_attack3_offscreen_decoy_does_not_pass_real_collapsed_fails() -> None:
    # Attack 3: an off-screen decoy (left:-99999) carries the active flag,
    # expanded full-width (ratio 1.0); the user-visible label stays width:0. The
    # decoy must not mint a pass, and the real on-screen collapsed label fails.
    obs = [
        _paint_obs(0, "New Pyramid", 95.0, 95.0, on_screen=False),  # decoy
        _paint_obs(0, "New Pyramid", 0.0, 95.0, on_screen=True),    # real, collapsed
    ]
    result = evaluate(PLAN, obs)
    assert result["status"] == "fail", result


def test_offscreen_decoy_alone_does_not_pass() -> None:
    # Even with no on-screen counterpart emitted, an off-screen "revealed" decoy
    # must not be counted as a passing observation.
    obs = [_paint_obs(0, "New Pyramid", 95.0, 95.0, on_screen=False)]
    result = evaluate(PLAN, obs)
    assert result["status"] != "pass", result


def test_painted_onscreen_reveal_passes() -> None:
    # Control: a faithful reveal — on-screen, painted, box ≈ content — passes.
    obs = [_paint_obs(0, "New Pyramid", 92.0, 95.0)]
    assert evaluate(PLAN, obs)["status"] == "pass"


# ── batch-9 ITEM 3: occlusion route — the gate must route observations through
# is_rendered (the shared multi-point paint-aware hit-test), not just on-screen +
# paint. Recreates /tmp/adv4-sr (an active label that expands and paints but is
# covered by an opaque overlay read PASS before the fix). Drives the REAL gate
# verdict, not just the shared primitive. ──


def test_occluded_active_label_fails_real_gate() -> None:
    # The active label expands to full content width AND paints readable text,
    # but an opaque node is the topmost paint at its rect (hitTest="blocked").
    # The reveal is invisible to the user — the gate verdict must FAIL.
    obs = [_paint_obs(60, "FAQs", 74.0, 74.0, hit_test="blocked")]
    result = evaluate(PLAN, obs)
    assert result["status"] == "fail", result
    bad = [r for r in result["rows"] if r["status"] == "fail"]
    assert bad and "occlud" in (bad[0].get("reason") or "").lower(), result["rows"]


def test_self_hittest_reveal_still_passes() -> None:
    # Control: the same faithful reveal with hitTest="self" (the element is the
    # topmost paint) must still PASS — the occlusion route adds no false-positive.
    obs = [_paint_obs(60, "FAQs", 74.0, 74.0, hit_test="self")]
    assert evaluate(PLAN, obs)["status"] == "pass"


# ── batch-9 ITEM 3 (Codex BLOCKER): provenance is honest at the PRODUCER ──
# main() stamps runtimeScanned=true only when a live scan ran (env flag + a real
# receipt file). A hand-authored observed-file run through `verdict` produces
# runtimeScanned=false so the consumer can reject it.


def test_main_verdict_without_receipt_is_not_runtime_scanned(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED", raising=False)
    monkeypatch.delenv("UI_CLONE_STATE_REVEAL_SCAN_RECEIPT", raising=False)
    ref = tmp_path / "ref"
    ref.mkdir()
    observed = tmp_path / "obs.json"
    observed.write_text("[]", encoding="utf-8")
    main(["verdict", str(ref), str(observed)])
    data = json.loads((ref / "state-reveal.json").read_text(encoding="utf-8"))
    assert data["runtimeScanned"] is False
    assert data["scanReceipt"] is None


def test_main_verdict_with_live_receipt_is_runtime_scanned(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    receipt = tmp_path / ".state-reveal-scan-receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED", "1")
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_SCAN_RECEIPT", str(receipt.resolve()))
    ref = tmp_path / "ref"
    ref.mkdir()
    observed = tmp_path / "obs.json"
    observed.write_text("[]", encoding="utf-8")
    main(["verdict", str(ref), str(observed)])
    data = json.loads((ref / "state-reveal.json").read_text(encoding="utf-8"))
    assert data["runtimeScanned"] is True
    assert data["scanReceipt"] == str(receipt.resolve())


def test_main_verdict_env_flag_but_missing_receipt_is_not_scanned(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # env flag set but the receipt file does not exist — a self-attested flag
    # with no browser-written receipt cannot mint runtimeScanned.
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED", "1")
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_SCAN_RECEIPT", str(tmp_path / "nope.json"))
    ref = tmp_path / "ref"
    ref.mkdir()
    observed = tmp_path / "obs.json"
    observed.write_text("[]", encoding="utf-8")
    main(["verdict", str(ref), str(observed)])
    data = json.loads((ref / "state-reveal.json").read_text(encoding="utf-8"))
    assert data["runtimeScanned"] is False
    assert data["scanReceipt"] is None


# ── tools batch-7 ITEM 1: contrast / alpha-floor / font-floor (pixel truth) ──


def _wow(box, content, *, color=None, effective_bg=None, color_alpha=1.0, font_px=14.0):  # type: ignore[no-untyped-def]
    o = _paint_obs(0, "FAQs", box, content, color_alpha=color_alpha, font_px=font_px)
    o["color"] = color if color is not None else [255, 255, 255]
    o["effectiveBgColor"] = effective_bg if effective_bg is not None else [255, 255, 255]
    return o


def test_white_on_white_reveal_fails() -> None:
    # Proven round-2 false-PASS: active label expanded full width, opaque text,
    # but color == effective background — a human sees an empty pill.
    result = evaluate(PLAN, [_wow(80.0, 80.0)])
    assert result["status"] == "fail", result
    bad = [r for r in result["rows"] if r["status"] == "fail"]
    assert bad and "paints no visible text" in (bad[0].get("reason") or ""), result["rows"]


def test_low_alpha_reveal_fails() -> None:
    # colorAlpha=0.01 — effectively invisible but defeated the old binary alpha>0.
    result = evaluate(PLAN, [_wow(80.0, 80.0, color=[17, 17, 17], color_alpha=0.01)])
    assert result["status"] == "fail", result


def test_font_at_floor_reveal_fails() -> None:
    # fontSizePx == MIN_FONT (4) is unreadable; strict floor catches it.
    result = evaluate(PLAN, [_wow(80.0, 80.0, color=[17, 17, 17], font_px=4.0)])
    assert result["status"] == "fail", result


def test_distinct_color_reveal_passes() -> None:
    # control: real dark text on white still passes (no contrast false-positive).
    result = evaluate(PLAN, [_wow(92.0, 95.0, color=[17, 17, 17])])
    assert result["status"] == "pass", result


def test_white_inverted_label_over_dark_reveal_passes() -> None:
    # tools-batch-11 ITEM 2: a white-inverted active label over a DARK section
    # (the fixed nav's visual backdrop, not its DOM-ancestor body cream) is
    # genuinely visible and must PASS. Before the visual-stack effectiveBgColor
    # fix, the JS collector recorded body cream here, so white-on-cream read
    # invisible and false-failed (The Solution pct13/25). This pins the positive
    # outcome at the gate-consumer level; the white-on-white FAIL above still
    # guards the genuinely-invisible case (the fix corrects the INPUT bg, never
    # the MIN_CONTRAST threshold).
    result = evaluate(
        PLAN, [_wow(92.0, 95.0, color=[255, 255, 255], effective_bg=[17, 0, 0])]
    )
    assert result["status"] == "pass", result


# ── tools batch-7 ITEM 2: settled box (reveal-then-collapse past the window) ──


def test_reveal_then_collapse_uses_settled_box_fails() -> None:
    # The label reveals (box≈content) through the probe window then collapses to
    # 8px past the settle floor; the settled box (8) is measured, not the 80
    # transient — ratio 0.11 < 0.5 => fail.
    o = _paint_obs(0, "FAQs", 80.0, 74.0)
    o["boxSamples"] = [80.0, 80.0, 8.0]
    result = evaluate(PLAN, [o])
    assert result["status"] == "fail", result


def test_stable_revealed_box_samples_pass() -> None:
    o = _paint_obs(0, "FAQs", 74.0, 74.0)
    o["boxSamples"] = [74.0, 74.0, 74.0]
    assert evaluate(PLAN, [o])["status"] == "pass"


# ── tools batch-7 ITEM 4: env-threshold clamping ──────────────────────


def test_reveal_ratio_env_clamped_to_band(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_RATIO", "0.01")
    assert reveal_ratio() == 0.4  # clamped up to the floor
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_RATIO", "5")
    assert reveal_ratio() == 0.95  # clamped down to the ceiling


def test_min_content_env_clamped_to_band(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("UI_CLONE_STATE_REVEAL_MIN_CONTENT_PX", "100")
    assert min_content_px() == 40.0


def test_evaluate_reclamps_low_ratio_plan() -> None:
    # UI_CLONE_STATE_REVEAL_RATIO=0.01 reached the plan; evaluate re-clamps to
    # 0.4 so the loop-11 defect (ratio 0.11) still fails.
    plan = {"expansions": _DEFECT_PLAN_EXP, "revealRatio": 0.01, "minContentPx": 12.0}
    result = evaluate(plan, [dict(_DEFECT_OBS)])
    assert result["status"] == "fail", result
    assert result["effectiveRevealRatio"] == 0.4


def test_evaluate_reclamps_high_min_content_plan() -> None:
    plan = {"expansions": _DEFECT_PLAN_EXP, "revealRatio": 0.5, "minContentPx": 100.0}
    result = evaluate(plan, [dict(_DEFECT_OBS)])
    assert result["status"] == "fail", result
    assert result["effectiveMinContentPx"] == 40.0


def test_evaluate_falsy_zero_ratio_clamped_not_defaulted() -> None:
    # 0.0 must clamp to the floor (0.4), not be silently swapped for 0.5 by the
    # old `or DEFAULT` truthiness bug.
    plan = {"expansions": _DEFECT_PLAN_EXP, "revealRatio": 0.0, "minContentPx": 0.0}
    result = evaluate(plan, [dict(_DEFECT_OBS)])
    assert result["status"] == "fail", result
    assert result["effectiveRevealRatio"] == 0.4
    assert result["effectiveMinContentPx"] == 4.0


def test_build_plan_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    (tmp_path / "bundle-extraction.json").write_text(json.dumps(EXTRACTION), encoding="utf-8")
    (tmp_path / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "nav-scroll-state-machine", "trigger": "scroll state machine"}]}),
        encoding="utf-8",
    )
    plan = build_plan(tmp_path)
    assert plan["selectors"] == [".nav_label_container__okVKb"]
    assert plan["hasStateMachine"] is True


def test_main_verdict_warn_is_surfaced_on_stderr(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """F12: a 'warn' verdict (a declared active-state reveal produced no passing
    observation) is deliberately non-blocking (exit 0, could be an unreachable impl
    URL, not an impl defect). But it must be SURFACED on stderr so an exit-code-only
    consumer does not read it as a clean pass identical to a real pass."""
    monkeypatch.delenv("UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED", raising=False)
    monkeypatch.delenv("UI_CLONE_STATE_REVEAL_SCAN_RECEIPT", raising=False)
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-extraction.json").write_text(json.dumps(EXTRACTION), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps({"transitions": []}), encoding="utf-8")
    observed = tmp_path / "obs.json"
    observed.write_text("[]", encoding="utf-8")  # declared expansion, no observation -> warn

    rc = main(["verdict", str(ref), str(observed)])
    data = json.loads((ref / "state-reveal.json").read_text(encoding="utf-8"))
    assert data["status"] == "warn", data
    assert rc == 0, "warn is non-blocking by design"
    err = capsys.readouterr().err
    assert "WARN" in err and "state-reveal" in err, f"warn not surfaced on stderr; stderr={err!r}"
