"""Ref-vs-ref SELF-PASS meta-check (tools-batch-11 ITEM 5).

THE MISSING VERIFICATION AXIS. Six adversarial rounds (batch-5..10) verified the
gates against BYPASS (a cheating impl must FAIL) and FALSE-POSITIVE (an honest
impl must PASS) — but never against ACHIEVABILITY: *can a correct impl satisfy
the gate at all, through the real pipeline?* A gate that consumes an artifact no
pipeline step produces, or computes an input from the wrong layer, fails the
REFERENCE against its own ground truth — and no bypass/false-positive panel
catches that, because those panels fed gate inputs directly instead of producing
them. loop-e2e-12 hit exactly this on four gates (ITEM 1-4).

The invariant this file enforces: **run every gate with the REFERENCE as the
impl and it MUST PASS** — the reference trivially matches itself. Any gate that
fails ref-vs-ref has an achievability bug and must not ship. Each gate below is
checked in the ACHIEVABILITY scenario that loop-e2e-12 exposed (not a trivial
all-visible clone, which would not exercise the gap), paired with a real-defect
NEGATIVE so the self-pass relaxation never blunts detection.

This runs in CI (pure-python gate verdicts, no browser). The decisive
full-pipeline "live ref as impl" proof — every check SCRIPT run with the live
reference URL as the impl URL — lives in
``scripts/ci/ref-vs-ref-selfpass.sh`` (opt-in; the frozen ref corpus is
gitignored so it cannot run on CI).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ui_clone.gates.hover_probe import evaluate_entry as hover_evaluate_entry
from ui_clone.gates.masked_region_static import (
    build_ref_viewport_visibility,
)
from ui_clone.gates.masked_region_static import (
    evaluate as masked_evaluate,
)
from ui_clone.gates.state_reveal import evaluate as state_evaluate
from ui_clone.section_compare_sections import pair_sections

# ── masked-region-static (ITEM 1): a ref that responsive/scroll-hides a masked
# selector at a probed viewport must pass against its own ref-viewport-visibility,
# generated from the SAME live-ref probe. ──
_MSEL = ".nav_pill_label"


def _ref_scaffold_entry() -> dict[str, Any]:
    return {
        "selector": _MSEL, "index": 0, "tag": "span", "classSig": "",
        "display": "block", "styles": {"text-align": "center"},
    }


def _rich(vp: int, *, visible: bool) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "selector": _MSEL, "index": 0, "tag": "span", "classSig": "",
        "display": "block" if visible else "none",
        "visibility": "visible", "opacity": 1.0,
        "rect": ({"top": 100, "left": 24, "width": 90, "height": 20}
                 if visible else {"top": 0, "left": 0, "width": 0, "height": 0}),
        "colorAlpha": 1.0, "fontSizePx": 16.0, "hasText": True,
        "clientWidth": vp, "styles": {"text-align": "center"},
    }
    return rec


def test_masked_region_static_ref_vs_ref_self_passes() -> None:
    # The live ref shows the masked label at 1280 and responsive-hides it at 375.
    # Probing the LIVE REF as the impl yields the SAME buckets; the producer
    # records 375 hidden, so the verdict excuses it instead of "impl element
    # absent". Self-pass.
    ref_entries = [_ref_scaffold_entry()]
    ref_probe = [_rich(1280, visible=True), _rich(375, visible=False)]
    vis = build_ref_viewport_visibility(ref_probe, [_MSEL], [375, 1280])
    result = masked_evaluate(
        ref_entries, ref_probe, ["text-align"],
        ref_hidden_viewports=vis["hiddenViewports"],
        ref_measured_viewports=vis["capturedViewports"],
    )
    assert result["status"] == "pass", result


def test_masked_region_static_real_defect_still_fails() -> None:
    # Negative: an impl that hides the label at 1280 — where the REF SHOWS it —
    # is NOT excused (respbypass anti-cheat). The producer marks nothing hidden
    # (the ref shows it at both), so the missing 1280 bucket is "impl element
    # absent".
    ref_entries = [_ref_scaffold_entry()]
    ref_probe = [_rich(1280, visible=True), _rich(375, visible=True)]
    vis = build_ref_viewport_visibility(ref_probe, [_MSEL], [375, 1280])
    assert vis["hiddenViewports"] == {}
    impl_hides_desktop = [_rich(375, visible=True), _rich(1280, visible=False)]
    result = masked_evaluate(
        ref_entries, impl_hides_desktop, ["text-align"],
        ref_hidden_viewports=vis["hiddenViewports"],
        ref_measured_viewports=vis["capturedViewports"],
    )
    assert result["status"] == "fail", result
    assert any(
        (r.get("reason") or "").startswith("impl element absent") for r in result["rows"]
    ), result


# ── state-reveal (ITEM 2): a white-INVERTED active label over a dark section is
# genuinely visible; the visual-stack effectiveBgColor records the dark backdrop,
# so it passes. ──
_STATE_PLAN = {
    "expansions": [{"selector": ".nav_label", "property": "width", "from": "0", "to": "auto"}],
    "revealRatio": 0.5,
    "minContentPx": 12.0,
}


def _state_obs(*, color: list[int], bg: list[int], box: float, content: float) -> dict[str, Any]:
    return {
        "selector": ".nav_label", "pct": 13, "text": "The Solution",
        "box": box, "content": content, "colorAlpha": 1.0, "opacity": 1.0,
        "fontSizePx": 16.0, "onScreen": True, "hasText": True,
        "color": color, "effectiveBgColor": bg,
    }


def test_state_reveal_white_on_dark_ref_vs_ref_self_passes() -> None:
    # white label, dark visual-stack backdrop (post visual-stack effectiveBgColor),
    # revealed box ~ content -> pass.
    obs = [_state_obs(color=[255, 255, 255], bg=[17, 0, 0], box=92.0, content=95.0)]
    assert state_evaluate(_STATE_PLAN, obs)["status"] == "pass"


def test_state_reveal_real_defect_still_fails() -> None:
    # Negatives: white-on-white (invisible) and a collapsed label both still FAIL
    # — the fix corrected the INPUT bg layer, not the contrast/ratio thresholds.
    invisible = [_state_obs(color=[255, 255, 255], bg=[255, 255, 255], box=92.0, content=95.0)]
    assert state_evaluate(_STATE_PLAN, invisible)["status"] == "fail"
    collapsed = [_state_obs(color=[255, 255, 255], bg=[17, 0, 0], box=8.0, content=95.0)]
    assert state_evaluate(_STATE_PLAN, collapsed)["status"] == "fail"


# ── hover-fallback (ITEM 3): a scroll-revealed JS-hover target, once the probe
# reaches it (scroll + belowFoldOk) and dispatches pointer events, produces an
# end-state delta -> verified. ──
def _hover_sample(*, found: bool, transform_after: str | None = None) -> dict[str, Any]:
    return {
        "found": found, "cssHoverProps": [],
        "before": {"width": 100.0, "bg": "rgb(1,1,1)", "color": "c1", "opacity": 1.0, "transform": "none"},
        "after": {"width": 100.0, "bg": "rgb(1,1,1)", "color": "c1", "opacity": 1.0,
                  "transform": transform_after or "none"},
    }


def test_hover_scroll_revealed_js_ref_vs_ref_self_passes() -> None:
    entry = {"id": "deck", "selectors": [".resources_deck"], "channels": ["transform"]}
    verdict = hover_evaluate_entry(
        entry, _hover_sample(found=True, transform_after="matrix(1.25,0,0,1.25,0,0)")
    )
    assert verdict["status"] == "verified", verdict


def test_hover_missing_behavior_still_fails() -> None:
    entry = {"id": "deck", "selectors": [".resources_deck"], "channels": ["transform"]}
    # resolved but no delta + no :hover rule -> still fail (no minted coverage).
    assert hover_evaluate_entry(entry, _hover_sample(found=True))["status"] == "fail"


# ── section-compare (ITEM 4): with impl == ref, every section pairs to ITSELF,
# even when two sections share a generic id; no cross-pair, no MISMATCHED-PAIR. ──
def _section(index: int, sid: str, cls: str) -> dict[str, Any]:
    return {
        "index": index, "tag": "footer", "id": sid, "className": cls,
        "fingerprint": "", "textWords": "",
        "rect": {"top": 8000 + index * 400, "left": 0, "width": 1440, "height": 400},
        "childCount": 3,
    }


def test_section_compare_duplicate_id_ref_vs_ref_self_passes() -> None:
    # Two sections share id="footer"; impl is an exact copy of ref. Each ref must
    # pair to its OWN copy (same class signature), never cross-pair.
    ref = [_section(0, "footer", "cta footer"), _section(1, "footer", "eatreal footer")]
    impl = [_section(0, "footer", "cta footer"), _section(1, "footer", "eatreal footer")]
    matches = pair_sections(ref, impl)
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}
    assert by_ref[0]["impl"] and by_ref[0]["impl"]["index"] == 0, by_ref[0]
    assert by_ref[1]["impl"] and by_ref[1]["impl"]["index"] == 1, by_ref[1]
    assert not any(m.get("status") == "UNMATCHED" for m in matches), matches


def test_section_compare_dropped_section_still_unmatched() -> None:
    # Negative: an impl that DROPS one of the two same-id sections leaves a ref
    # UNMATCHED (a real defect the gate fails on), not a vacuous self-pair.
    ref = [_section(0, "footer", "cta footer"), _section(1, "footer", "eatreal footer")]
    impl = [_section(0, "footer", "cta footer")]
    matches = pair_sections(ref, impl)
    by_ref = {m["ref"]["index"]: m for m in matches if m.get("ref")}
    assert by_ref[1].get("impl") is None and by_ref[1].get("status") == "UNMATCHED", by_ref[1]


# ── alignment-parity (batch-12 ITEM 3): the realfood foods/pyramid grid is a
# horizontally-OVERFLOWING scroll-track (child union >> container, clipped by
# overflow-x:clip). Its off-screen overflow extent / start offset is not a visible
# centering property and legitimately differs between two captures, so the
# per-group centering prongs EXEMPT overflow tracks. A non-overflow centered group
# that is off-center still fails. alignment-parity has no python verdict module, so
# this runs the real check SCRIPT (fast, no browser — CI-safe). ──
_AP_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills" / "visual-debug" / "scripts" / "alignment-parity-check.sh"
)


def _run_alignment_parity(matches: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        ref_dir = Path(td) / "ref"
        d = ref_dir / "sections" / "viewports" / "375x812" / "sections"
        d.mkdir(parents=True)
        (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")
        subprocess.run(
            ["bash", str(_AP_SCRIPT), str(ref_dir)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        result = json.loads((ref_dir / "alignment-parity.json").read_text(encoding="utf-8"))
        assert isinstance(result, dict)
        return result


def _ap_row(name: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "index": 0, "tag": "section", "id": None, "className": name,
        "fingerprint": "x", "textWords": "x",
        "rect": {"top": 100, "left": 0, "width": 375, "height": 600},
        "childCount": 3, "clientWidth": 375,
        "contentBox": {"left": 24, "width": 327, "boxCount": 3},
        "leftGap": 24, "rightGap": 24, "contentGroups": groups,
    }


def test_alignment_parity_overflow_ref_vs_ref_self_passes() -> None:
    # Two captures of the foods overflow strip: same container (338) but the
    # off-screen union/start differs (uL -217/uW 1368 vs -148/1457). The overflow
    # exemption makes it self-pass; the visible box is still measured.
    ref = _ap_row("foods", [{
        "name": "dga_foods_inner", "containerLeft": 24, "containerWidth": 338,
        "unionLeft": -217, "unionWidth": 1368, "childCount": 5,
        "childCenters": [-150, 100, 350, 600, 900]}])
    impl = _ap_row("foods", [{
        "name": "dga_foods_inner", "containerLeft": 24, "containerWidth": 338,
        "unionLeft": -148, "unionWidth": 1457, "childCount": 5,
        "childCenters": [-80, 180, 440, 700, 1010]}])
    art = _run_alignment_parity([{"name": "foods", "score": 1.0, "ref": ref, "impl": impl}])
    assert art["status"] == "pass", art
    assert any(r["check"] == "group-overflow" for r in art["rows"]), art


def test_alignment_parity_centered_offcenter_still_fails() -> None:
    # Negative: a FITTING (non-overflow) group off-center beyond tolerance must
    # still FAIL — the overflow exemption never relaxes a centering defect.
    ref = _ap_row("strip", [{
        "name": "cards", "containerLeft": 0, "containerWidth": 375,
        "unionLeft": 40, "unionWidth": 295, "childCount": 3}])
    impl = _ap_row("strip", [{
        "name": "cards", "containerLeft": 0, "containerWidth": 375,
        "unionLeft": 80, "unionWidth": 295, "childCount": 3}])  # +40 off-center, fits
    art = _run_alignment_parity([{"name": "strip", "score": 1.0, "ref": ref, "impl": impl}])
    assert art["status"] == "fail", art
