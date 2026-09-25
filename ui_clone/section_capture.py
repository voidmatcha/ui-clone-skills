"""Safe section screenshot capture helpers for section-compare.sh.

The shell wrapper delegates matched-section capture here so selector-derived
section names and scroller selectors are handled as data, not shell syntax.

Primitives live in sibling modules (``section_capture_primitives``,
``section_capture_js``, ``section_capture_browser``) and are re-exported here.
The capture orchestration (``_capture_one`` / ``capture_matched_sections``)
and the composite steps whose callees tests patch by this module's name
(``_run_screenshot``, ``_run_crop``, ``_apply_reference_runtime_normalization``)
stay here so ``monkeypatch.setattr(ui_clone.section_capture, ...)`` keeps
intercepting them.
"""

from __future__ import annotations

import json
import os
import re  # noqa: F401 - kept as a module attribute for import-compat
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ui_clone.pipeline_logs import _as_text  # noqa: F401 - kept for import-compat
from ui_clone.section_capture_browser import (
    _AGENT_BROWSER_TIMEOUT,
    _ensure_viewport,
    _resolve_live_section_rect,
    _run_agent_browser,
    _run_agent_eval,
    _run_agent_eval_text,
    _scroll_metrics,
    _unwrap_eval_json,
)
from ui_clone.section_capture_js import (
    CMP_OVERLAY_SELECTORS,
    _canvas_underlay_js,
    _disable_smooth_scroll_js,
    _finish_js,
    _fixed_overlay_toggle_js,
    _live_section_rect_js,
    _pause_js,
    _reference_runtime_normalization_js,
    _scroll_js,
    _scroll_metrics_js,
    _settle_js,
)
from ui_clone.section_capture_primitives import (
    _MULTI_UNDERSCORE_RE,
    _SAFE_NAME_RE,
    _as_float,
    _canvas_height,
    _crop_is_blank,
    _duration_to_seconds,
    _fmt_num,
    _is_number,
    _rect_from_capture,
    crop_is_off_canvas,
    crop_unique_colors,
    derive_settle_seconds,
    desired_scroll_y,
    safe_section_name,
    should_pin_to_bottom,
    write_transparent_stub,
)

if TYPE_CHECKING:
    from typing import TypeGuard  # noqa: F401 - kept for import-compat

__all__ = [
    "CMP_OVERLAY_SELECTORS",
    "_AGENT_BROWSER_TIMEOUT",
    "_MULTI_UNDERSCORE_RE",
    "_SAFE_NAME_RE",
    "_apply_reference_runtime_normalization",
    "_as_float",
    "_canvas_height",
    "_canvas_underlay_js",
    "_capture_one",
    "_crop_is_blank",
    "_disable_smooth_scroll_js",
    "_duration_to_seconds",
    "_ensure_viewport",
    "_finish_js",
    "_fixed_overlay_toggle_js",
    "_fmt_num",
    "_is_number",
    "_live_section_rect_js",
    "_pause_js",
    "_rect_from_capture",
    "_reference_runtime_normalization_js",
    "_resolve_live_section_rect",
    "_run_agent_browser",
    "_run_agent_eval",
    "_run_agent_eval_text",
    "_run_crop",
    "_run_screenshot",
    "_scroll_js",
    "_scroll_metrics",
    "_scroll_metrics_js",
    "_settle_js",
    "_unwrap_eval_json",
    "capture_matched_sections",
    "crop_is_off_canvas",
    "crop_unique_colors",
    "derive_settle_seconds",
    "desired_scroll_y",
    "main",
    "safe_section_name",
    "should_pin_to_bottom",
    "write_transparent_stub",
]


def _apply_reference_runtime_normalization(session: str) -> None:
    js = _reference_runtime_normalization_js()
    if js == "undefined":
        return
    if os.environ.get("SECTION_CAPTURE_REQUIRE_SCROLL_CAP_NORMALIZED") != "1":
        _run_agent_eval(session, js)
        return
    result = _unwrap_eval_json(_run_agent_eval_text(session, js))
    cap_selector = (os.environ.get("REF_SCROLL_CAP_SELECTOR") or "").strip()
    if result is None:
        raise RuntimeError("reference scroll-cap normalization returned no evidence")
    if cap_selector and (
        _as_float(result.get("capCount")) < 1
        or _as_float(result.get("residualCap"), -1.0) != 0
    ):
        raise RuntimeError(
            "reference scroll-cap normalization did not hold "
            f"for selector {cap_selector!r}: {result}"
        )


def _run_screenshot(session: str, output_path: Path) -> None:
    last_error = ""
    for attempt in range(3):
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        result = _run_agent_browser(
            ["agent-browser", "--session", session, "screenshot", str(output_path)]
        )
        height = _canvas_height(output_path)
        if result.returncode == 0 and height > 2:
            return
        last_error = (
            f"attempt={attempt + 1} exit={result.returncode} height={height:g} "
            f"stderr={(result.stderr or '').strip()[-300:]}"
        )
        time.sleep(0.15)
    raise RuntimeError(f"section screenshot invalid after 3 attempts: {last_error}")


def _run_crop(image_path: Path, rect: dict[str, object], clip_top: float) -> None:
    crop_h = min(_as_float(rect.get("height")), 1800.0)
    width = _as_float(rect.get("width"))
    left = _as_float(rect.get("left"))
    canvas_h = _canvas_height(image_path)
    if canvas_h > 0 and crop_is_off_canvas(
        clip_top=clip_top, crop_h=crop_h, canvas_h=canvas_h
    ):
        write_transparent_stub(image_path)
        return
    # Partial-overlap clamp (batch-13 ITEM 1 sub-fix 2). A near-bottom section
    # pinned to maxScroll has its TOP scrolled above the viewport (clip_top < 0)
    # while its content sits in the visible band below — the observed site's "Eat Real
    # Cheese" footer reveals only at maxScroll, so it can't be top-aligned, yet a
    # raw negative clip_top crops black padding that quantizes to a flat
    # "content never rendered" band. Clamp to the VISIBLE portion (drop the
    # off-viewport-top rows) so the crop captures the revealed content. Symmetric
    # on ref+impl, so it cannot hide a one-sided defect. (Wholly-off-viewport
    # sections were already turned into 1x1 stubs by the off-canvas check above.)
    if clip_top < 0:
        crop_h = max(0.0, crop_h + clip_top)
        clip_top = 0.0
    geometry = f"{_fmt_num(width)}x{_fmt_num(crop_h)}+{_fmt_num(left)}+{_fmt_num(clip_top)}"
    subprocess.run(
        ["magick", str(image_path), "-crop", geometry, "+repage", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _capture_one(
    *,
    session: str,
    section_dir: Path,
    side: str,
    name: str,
    rect: dict[str, object],
    scroller_selector: str,
    pause_js: str,
    finish_js: str,
    skip_finish: bool,
    wait_scroll_settle: float,
    identity: dict[str, object] | None = None,
    forced_scroll_y: float | None = None,
    forced_foreground_roi: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    top = _as_float(rect.get("top"))
    height = _as_float(rect.get("height"))

    # Plan the scroll from real page metrics. Near-end sections pin to
    # maxScroll (the request would clamp there anyway, and end-of-page
    # reveal latches only mount once the page is actually at the end).
    factor = _as_float(os.environ.get("SECTION_CAPTURE_BOTTOM_ANCHOR_FACTOR"), 1.5)
    is_reference = side.startswith("ref") or (
        side == "impl"
        and os.environ.get("SECTION_CAPTURE_IMPL_IS_REFERENCE") == "1"
    )
    if is_reference:
        _apply_reference_runtime_normalization(session)
    metrics = _scroll_metrics(session, scroller_selector)
    if forced_scroll_y is not None:
        # batch-13 ITEM 1 — CAPTURE DETERMINISM. Reuse the EXACT scroll position
        # the frozen-ref capture used so the impl lands on the SAME framer
        # scroll-scrub frame. The scrub is window.scrollY-driven; recomputing the
        # impl scroll_y independently lands a different phase and inflates AE on
        # identical content (the observed site's pyramid-zoom class: ref-vs-ref-calib
        # AE 0 but frozen-ref-vs-live-impl AE 166897). Detection is preserved: a
        # broken impl rendered at the SAME scroll still diverges and fails.
        scroll_y = forced_scroll_y
        pinned = False
    elif metrics is not None:
        scroll_y = desired_scroll_y(
            top=top, height=height, scroll_height=metrics["sh"],
            viewport_h=metrics["vh"], factor=factor,
        )
        pinned = should_pin_to_bottom(
            top=top, height=height, scroll_height=metrics["sh"],
            viewport_h=metrics["vh"], factor=factor,
        )
    else:
        scroll_y = max(0.0, top - 50.0)
        pinned = False

    def _settle_and_shoot(
        target_y: float, output_path: Path
    ) -> tuple[dict[str, Any] | None, float, dict[str, Any]]:
        if is_reference:
            _apply_reference_runtime_normalization(session)
        # Kill Lenis/smooth-scroll first so the forced scroll is not reverted to
        # actualY=0 during settle (specific regression cross-impl scroll-mapping class).
        _run_agent_eval(session, _disable_smooth_scroll_js())
        _run_agent_eval(session, _scroll_js(target_y, scroller_selector))
        _run_agent_eval(session, _fixed_overlay_toggle_js(target_y > 0))
        time.sleep(0.1)
        _run_agent_eval(session, pause_js)
        _run_agent_eval(session, _canvas_underlay_js())
        conf: dict[str, Any] | None = None
        if not skip_finish:
            _run_agent_eval(session, finish_js)
            conf = _unwrap_eval_json(_run_agent_eval_text(session, _settle_js()))
        time.sleep(max(0.2, wait_scroll_settle))
        # V-1: assert the session viewport immediately before the shot.
        expect_w_raw = (os.environ.get("SECTION_CAPTURE_VIEW_W") or "").strip()
        if expect_w_raw.isdigit():
            _ensure_viewport(session, int(expect_w_raw))
        if is_reference:
            # Page-owned scroll handlers can restore the cap during settle.
            # Reapply it before measuring actualY and resolving the live rect.
            _apply_reference_runtime_normalization(session)
        # Clip from the ACTUAL position after the viewport assertion. A viewport
        # repair can itself reflow the page and clamp scrollY, so measuring
        # before `_ensure_viewport` would pair a fresh live rect with stale
        # scroll metrics.
        post = _scroll_metrics(session, scroller_selector)
        actual_y = post["y"] if post is not None else target_y
        planned_crop_top = top - actual_y
        live_rect = _resolve_live_section_rect(session, identity, top)
        crop_rect = live_rect if live_rect is not None else rect
        crop_top = _as_float(crop_rect.get("top")) if live_rect is not None else planned_crop_top
        crop_meta: dict[str, Any] = {
            "plannedCropTop": planned_crop_top,
            "liveRectResolved": live_rect is not None,
        }
        if live_rect is not None:
            crop_meta["liveCropRect"] = live_rect
            crop_meta["cropDriftPx"] = crop_top - planned_crop_top
            for key in ("bottomSticky", "hitVisible"):
                if isinstance(live_rect.get(key), bool):
                    crop_meta[key] = live_rect[key]
            if isinstance(live_rect.get("position"), str):
                crop_meta["position"] = live_rect["position"]
            own_foreground_roi = _rect_from_capture(live_rect.get("foregroundRoi"))
            if own_foreground_roi is not None:
                crop_meta["foregroundRoi"] = own_foreground_roi
            for key in ("foregroundRectCount", "underlayCanvasCount"):
                if _is_number(live_rect.get(key)):
                    crop_meta[key] = int(_as_float(live_rect[key]))
            if _is_number(live_rect.get("hitVisibleSamples")):
                crop_meta["hitVisibleSamples"] = int(
                    _as_float(live_rect["hitVisibleSamples"])
                )
            if _is_number(live_rect.get("stickyEndScrollY")):
                crop_meta["stickyEndScrollY"] = _as_float(
                    live_rect["stickyEndScrollY"]
                )
        roi_path = section_dir / "foreground-roi" / side / f"{name}.png"
        roi_path.unlink(missing_ok=True)
        _run_screenshot(session, output_path)
        own_roi = _rect_from_capture(crop_meta.get("foregroundRoi"))
        roi_to_use = forced_foreground_roi or own_roi
        if roi_to_use is not None:
            roi_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output_path, roi_path)
            _run_crop(roi_path, dict(roi_to_use), roi_to_use["top"])
            crop_meta["foregroundRoiUsed"] = roi_to_use
        _run_crop(output_path, crop_rect, crop_top)
        return conf, actual_y, crop_meta

    output_path = section_dir / side / f"{name}.png"
    confidence, actual_y, crop_meta = _settle_and_shoot(scroll_y, output_path)
    meta: dict[str, Any] = dict(confidence or {})
    meta.update(crop_meta)
    # Record the scroll position used so the frozen-ref capture can hand it to a
    # later impl capture for scroll-scrub determinism (see capture_matched_sections).
    meta["actualY"] = actual_y
    if pinned:
        meta["bottomAnchored"] = True

    # A bottom-sticky section can occupy a viewport-shaped rect long before its
    # content is actually exposed: page content with a higher stacking context
    # paints over it until the sticky containing block reaches its end. Hit
    # testing cannot prove paint ownership because pointer-events:none overlays
    # are omitted from elementsFromPoint. Instead, use the live sticky owner's
    # containing-block end position. Ordinary top-sticky elements, bottom-sticky
    # elements already at their end position, and unresolved identities retain
    # their old path.
    sticky_end_pinned = False
    sticky_end_scroll_y = crop_meta.get("stickyEndScrollY")
    if (
        forced_scroll_y is None
        and not pinned
        and metrics is not None
        and crop_meta.get("liveRectResolved") is True
        and crop_meta.get("bottomSticky") is True
        and _is_number(sticky_end_scroll_y)
    ):
        max_scroll = max(0.0, metrics["sh"] - metrics["vh"])
        flow_scroll = min(max_scroll, max(0.0, _as_float(sticky_end_scroll_y)))
        if actual_y < flow_scroll - 1.0:
            retry_conf, retry_y, crop_meta = _settle_and_shoot(flow_scroll, output_path)
            meta = dict(retry_conf or {})
            meta.update(crop_meta)
            meta["actualY"] = retry_y
            meta["bottomAnchored"] = True
            meta["stickyEndRecapture"] = True
            pinned = True
            sticky_end_pinned = True

    # A content-bearing section can be correctly identified yet visually empty
    # at its first anchor: fixed chrome may reveal only after scrolling, and a
    # tall section may keep its visible children around the middle of its flow
    # box. Seek a measurable state on the reference and persist that exact
    # scrollY for the implementation capture. This changes capture position,
    # never thresholds or verdicts; if no candidate carries signal, restore the
    # original frame so the existing UNMEASURED guard remains fail-closed.
    if (
        forced_scroll_y is None
        and not pinned
        and metrics is not None
        and crop_meta.get("liveRectResolved") is True
    ):
        flat_max = int(
            _as_float(os.environ.get("SECTION_CAPTURE_FLAT_RETRY_MAX_COLORS"), 4.0)
        )
        initial_unique = crop_unique_colors(output_path)
        initial_blank = _crop_is_blank(output_path)
        initial_content_free = initial_blank or (
            initial_unique is not None and initial_unique <= flat_max
        )
        if initial_content_free:
            max_scroll = max(0.0, metrics["sh"] - metrics["vh"])
            position = str(crop_meta.get("position") or "")
            if position == "fixed":
                raw_targets = [metrics["vh"] * 0.75, metrics["vh"] * 1.5]
            else:
                raw_targets = [
                    top + height * ratio - metrics["vh"] / 2.0
                    for ratio in (0.25, 0.5, 0.75)
                ]
            targets: list[float] = []
            for candidate in raw_targets:
                target = min(max_scroll, max(0.0, candidate))
                if abs(target - actual_y) <= 1.0 or any(
                    abs(target - prior) <= 1.0 for prior in targets
                ):
                    continue
                targets.append(target)

            original_y = actual_y
            recovered = False
            for target in targets:
                retry_conf, retry_y, retry_meta = _settle_and_shoot(
                    target, output_path
                )
                retry_unique = crop_unique_colors(output_path)
                retry_blank = _crop_is_blank(output_path)
                if not retry_blank and (
                    retry_unique is None or retry_unique > flat_max
                ):
                    meta = dict(retry_conf or {})
                    meta.update(retry_meta)
                    meta["actualY"] = retry_y
                    meta["signalRecovery"] = True
                    meta["signalRecoveryTarget"] = target
                    if initial_unique is not None:
                        meta["signalRecoveryUniqueBefore"] = initial_unique
                    if retry_unique is not None:
                        meta["signalRecoveryUniqueAfter"] = retry_unique
                    actual_y = retry_y
                    crop_meta = retry_meta
                    recovered = True
                    break
            if targets and not recovered:
                restore_conf, restore_y, restore_meta = _settle_and_shoot(
                    original_y, output_path
                )
                meta = dict(restore_conf or {})
                meta.update(restore_meta)
                meta["actualY"] = restore_y
                crop_meta = restore_meta

    # Content-free retry: a crop that quantizes to a handful of colors on a
    # section that has content means the capture window missed the content
    # (scroll-latched reveals). Re-capture pinned to maxScroll when the
    # section still intersects the bottom viewport. Skipped when the scroll is
    # FORCED — determinism must win (the ref captured content at this exact
    # position, so the impl will too; moving the impl to maxScroll would break
    # the scrub-frame match the force exists to guarantee).
    if forced_scroll_y is None and not pinned and metrics is not None:
        flat_max = int(_as_float(os.environ.get("SECTION_CAPTURE_FLAT_RETRY_MAX_COLORS"), 4.0))
        uniq = crop_unique_colors(output_path)
        max_scroll = max(0.0, metrics["sh"] - metrics["vh"])
        intersects_bottom_view = top + height > max_scroll
        if uniq is not None and uniq <= flat_max and intersects_bottom_view:
            retry_conf, retry_y, crop_meta = _settle_and_shoot(max_scroll, output_path)
            meta = dict(retry_conf or {})
            meta.update(crop_meta)
            meta["actualY"] = retry_y
            meta["flatRecapture"] = True
            meta["flatRecaptureUniqueBefore"] = uniq

    # Near-bottom blank retry (batch-13 ITEM 1 sub-fix 2). A section whose BOTTOM
    # sits near the page end is pinned to maxScroll, but a TALL section whose
    # CONTENT lives at its TOP (a bottom credit footer, a CTA block) then has
    # maxScroll scroll PAST that content: the crop band lands off-canvas
    # (1x1 stub) or on empty footer background (std ~0), surfacing as a blank-ref
    # UNMEASURED that blocks the gate. When the section top is actually reachable
    # (top < maxScroll), re-shoot TOP-ALIGNED so the content is captured, and
    # record the position so the frozen impl + calib passes reuse it (keeping all
    # three crops on the same band). Ref pass only (forced impl reuses the result).
    if (
        forced_scroll_y is None
        and pinned
        and not sticky_end_pinned
        and metrics is not None
    ):
        max_scroll = max(0.0, metrics["sh"] - metrics["vh"])
        flat_max = int(_as_float(os.environ.get("SECTION_CAPTURE_FLAT_RETRY_MAX_COLORS"), 4.0))
        uniq = crop_unique_colors(output_path)
        is_content_free = _crop_is_blank(output_path) or (
            uniq is not None and uniq <= flat_max
        )
        if top < max_scroll - 1.0 and is_content_free:
            top_aligned = min(max_scroll, max(0.0, top - 50.0))
            retry_conf, retry_y, crop_meta = _settle_and_shoot(top_aligned, output_path)
            meta = dict(retry_conf or {})
            meta.update(crop_meta)
            meta["actualY"] = retry_y
            meta["topAlignedRetry"] = True
            if uniq is not None:
                meta["topAlignedRetryUniqueBefore"] = uniq

    return meta if meta else confidence


def capture_matched_sections(matches: list[dict[str, Any]]) -> int:
    section_dir = Path(os.environ["SECTION_CAPTURE_DIR"]) / "sections"
    session_ref = os.environ["SECTION_CAPTURE_SESSION_REF"]
    session_impl = os.environ["SECTION_CAPTURE_SESSION_IMPL"]
    ref_scroller = os.environ.get("SECTION_CAPTURE_REF_SCROLLER_SEL", "__document__")
    impl_scroller = os.environ.get("SECTION_CAPTURE_IMPL_SCROLLER_SEL", "__document__")
    reuse_frozen_ref = os.environ.get("SECTION_CAPTURE_REUSE_FROZEN_REF", "0") == "1"
    # Freeze the exact pairing used to name and crop the live reference.
    # section-compare may rewrite matches.json later in the same multi-pass
    # workflow; promotion must follow the capture-time rows, not that mutable
    # path, or a name can be attached to a different section rectangle.
    if not reuse_frozen_ref:
        (section_dir / "frozen-capture-matches.json").write_text(
            json.dumps(matches, indent=2) + "\n",
            encoding="utf-8",
        )
    # batch-13 ITEM 1 — ref-instability calibration. When enabled (and capturing
    # the live ref), capture a SECOND reference frame per section after a page
    # reload into sections/ref-calib/. The reference's frame-to-frame variance
    # across two independent loads is what classifies a section as dynamic
    # (framer scroll-scrub / splash / carousel) downstream — measured on the
    # reference's OWN instability, never on the impl.
    ref_calib = os.environ.get("SECTION_CAPTURE_REF_CALIB", "0") == "1"
    ref_url = os.environ.get("SECTION_CAPTURE_REF_URL", "")
    calib_vw = os.environ.get("SECTION_CAPTURE_VIEW_W", "")
    calib_vh = os.environ.get("SECTION_CAPTURE_VIEW_H", "")
    skip_finish = os.environ.get("SECTION_CAPTURE_SKIP_FINISH", "0") == "1"
    wait_scroll_settle = _as_float(os.environ.get("SECTION_CAPTURE_WAIT_SCROLL_SETTLE"), 0.5)
    pause_js = _pause_js()
    finish_js = _finish_js()

    # batch-13 ITEM 1 — scroll-scrub capture determinism. The frozen-ref capture
    # records the exact scroll position per section into
    # sections/ref-scroll-positions.json; a later FROZEN-mode impl capture reuses
    # it so the impl lands on the SAME framer scroll-scrub frame instead of a
    # recomputed (divergent) one. Falls back to per-side computation when the
    # manifest is absent.
    positions_path = section_dir / "ref-scroll-positions.json"
    foreground_rois_path = section_dir / "ref-foreground-rois.json"
    forced_positions: dict[str, float] = {}
    if reuse_frozen_ref and positions_path.is_file():
        try:
            loaded = json.loads(positions_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                forced_positions = {
                    str(k): float(v)
                    for k, v in loaded.items()
                    if _is_number(v)
                }
        except (OSError, json.JSONDecodeError, ValueError):
            forced_positions = {}
    ref_positions: dict[str, float] = {}
    impl_positions: dict[str, float] = {}
    forced_foreground_rois: dict[str, dict[str, float]] = {}
    if reuse_frozen_ref and foreground_rois_path.is_file():
        try:
            raw_rois = json.loads(foreground_rois_path.read_text(encoding="utf-8"))
            if isinstance(raw_rois, dict):
                for key, value in raw_rois.items():
                    loaded_roi = _rect_from_capture(value)
                    if loaded_roi is not None:
                        forced_foreground_rois[str(key)] = loaded_roi
        except (OSError, json.JSONDecodeError):
            forced_foreground_rois = {}
    ref_foreground_rois: dict[str, dict[str, float]] = {}
    impl_foreground_rois: dict[str, dict[str, float]] = {}

    confidence_map: dict[str, dict[str, Any]] = {}
    # One section's screenshot failure (agent-browser exit != 0 or a <=2px
    # canvas after 3 attempts) must not abort the whole capture: the caller
    # runs under `set -euo pipefail` and every other section would lose its
    # crops. Record the failure per section/side instead; section-compare.sh
    # reports the missing crop as UNMEASURED (capture failed) from this sidecar.
    capture_failures: dict[str, dict[str, str]] = {}
    capture_failures_path = section_dir / "capture-failures.json"
    if reuse_frozen_ref and capture_failures_path.is_file():
        # The frozen pass does not re-shoot the reference; keep the ref-side
        # failures recorded by the pass that produced the frozen crops so the
        # missing ref crop is still reported as a capture failure.
        try:
            prior = json.loads(capture_failures_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = {}
        if isinstance(prior, dict):
            for key, sides in prior.items():
                if isinstance(sides, dict) and isinstance(sides.get("ref"), str):
                    capture_failures[str(key)] = {"ref": sides["ref"]}

    def _capture_side(side: str, name: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            return _capture_one(
                section_dir=section_dir,
                side=side,
                name=name,
                pause_js=pause_js,
                finish_js=finish_js,
                skip_finish=skip_finish,
                wait_scroll_settle=wait_scroll_settle,
                **kwargs,
            )
        except RuntimeError as exc:
            message = str(exc)
            # Drop any partial/invalid crop so downstream sees a MISSING crop,
            # never a stub image that could pass a vacuous comparison.
            (section_dir / side / f"{name}.png").unlink(missing_ok=True)
            (section_dir / "foreground-roi" / side / f"{name}.png").unlink(missing_ok=True)
            capture_failures.setdefault(name, {})[side] = message
            confidence_map.setdefault(name, {})[side] = {
                "captureFailed": True,
                "captureError": message,
            }
            sys.stdout.write(f"  ✗ {name} ({side}): capture failed — {message}\n")
            sys.stdout.flush()
            return None

    for match in matches:
        name = safe_section_name(match.get("name"))
        ref = match.get("ref")
        impl = match.get("impl")

        if isinstance(ref, dict) and not reuse_frozen_ref:
            rect = ref.get("rect")
            if isinstance(rect, dict):
                conf = _capture_side(
                    "ref",
                    name,
                    session=session_ref,
                    rect=rect,
                    scroller_selector=ref_scroller,
                    identity=ref,
                )
                if conf is not None:
                    confidence_map.setdefault(name, {})["ref"] = conf
                    _ay = conf.get("actualY")
                    if _is_number(_ay):
                        ref_positions[name] = float(_ay)
                    _roi = _rect_from_capture(conf.get("foregroundRoiUsed"))
                    if _roi is not None:
                        ref_foreground_rois[name] = _roi

        if isinstance(impl, dict) and name not in capture_failures:
            rect = impl.get("rect")
            if isinstance(rect, dict):
                conf = _capture_side(
                    "impl",
                    name,
                    session=session_impl,
                    rect=rect,
                    scroller_selector=impl_scroller,
                    identity=impl,
                    # The live reference may have moved away from the planned
                    # anchor to recover a content-bearing frame. Pair the
                    # implementation with that exact frame in this pass too;
                    # otherwise a recovered reference is compared with the
                    # implementation's original blank anchor.
                    forced_scroll_y=(
                        forced_positions.get(name)
                        if reuse_frozen_ref
                        else ref_positions.get(name)
                    ),
                    forced_foreground_roi=(
                        forced_foreground_rois.get(name)
                        if reuse_frozen_ref
                        else ref_foreground_rois.get(name)
                    ),
                )
                if conf is not None:
                    confidence_map.setdefault(name, {})["impl"] = conf
                    if not reuse_frozen_ref:
                        _ay = conf.get("actualY")
                        if _is_number(_ay):
                            impl_positions[name] = float(_ay)
                    _roi = _rect_from_capture(conf.get("foregroundRoiUsed"))
                    if _roi is not None:
                        impl_foreground_rois[name] = _roi

        if name in capture_failures:
            continue
        sys.stdout.write(f"  ✓ {name}\n")
        sys.stdout.flush()

    # Always rewrite the sidecar so a stale failure list from an earlier run
    # cannot mark a section that captured cleanly this time.
    (section_dir / "capture-failures.json").write_text(
        json.dumps(capture_failures, indent=2) + "\n", encoding="utf-8"
    )

    # Persist the ref scroll positions so a later frozen-mode impl capture lands
    # on the same scroll-scrub frame (batch-13 ITEM 1 capture determinism).
    # Empty results are current evidence too. Retaining an earlier manifest
    # would force a later frozen capture to reuse obsolete coordinates.
    if not reuse_frozen_ref:
        positions_path.write_text(
            json.dumps(ref_positions, indent=2) + "\n", encoding="utf-8"
        )
    (section_dir / "impl-scroll-positions.json").write_text(
        json.dumps(impl_positions, indent=2) + "\n",
        encoding="utf-8",
    )
    if not reuse_frozen_ref:
        foreground_rois_path.write_text(
            json.dumps(ref_foreground_rois, indent=2) + "\n", encoding="utf-8"
        )
    (section_dir / "impl-foreground-rois.json").write_text(
        json.dumps(impl_foreground_rois, indent=2) + "\n", encoding="utf-8"
    )

    # ── batch-13 ITEM 1: reference self-calibration frame (ref-calib) ──
    # A SECOND reference frame per section captured in a SEPARATE, independent
    # browser session. The cross-session page-load variance (different lazy
    # heights -> different scroll position -> different framer scrub frame) is
    # exactly what frozen-ref-vs-live-impl experiences; a same-session re-shoot
    # is deterministic at a fixed scrollY (selfAE 0) and would miss it. The
    # ref-vs-ref-calib divergence (computed downstream) classifies dynamic
    # sections by the reference's OWN instability — the impl is never involved.
    #
    # NB: the ref-vs-ref-selfpass meta-check supersedes this with an IMPL-PATH
    # calib (it captures the ref a second time through the impl path, which a
    # minimal/ref-path calib here cannot reproduce for scroll-scrub sections);
    # this branch remains for single-pass callers that opt in via SECTION_REF_CALIB.
    if ref_calib and not reuse_frozen_ref and ref_url:
        (section_dir / "ref-calib").mkdir(parents=True, exist_ok=True)
        calib_session = f"{session_ref}-cal"
        if calib_vw and calib_vh:
            _run_agent_browser(
                ["agent-browser", "--session", calib_session, "set", "viewport", calib_vw, calib_vh],
            )
        _run_agent_browser(
            ["agent-browser", "--session", calib_session, "open", ref_url],
        )
        _run_agent_browser(
            ["agent-browser", "--session", calib_session, "wait", "2500"],
        )
        for match in matches:
            name = safe_section_name(match.get("name"))
            ref = match.get("ref")
            if not isinstance(ref, dict):
                continue
            rect = ref.get("rect")
            if not isinstance(rect, dict):
                continue
            try:
                _capture_one(
                    session=calib_session,
                    section_dir=section_dir,
                    side="ref-calib",
                    name=name,
                    rect=rect,
                    scroller_selector=ref_scroller,
                    pause_js=pause_js,
                    finish_js=finish_js,
                    skip_finish=skip_finish,
                    wait_scroll_settle=wait_scroll_settle,
                    identity=ref,
                )
            except RuntimeError as exc:
                # A missing calib crop only disables dynamic classification for
                # this section (strict AE stays in force); never abort the run.
                (section_dir / "ref-calib" / f"{name}.png").unlink(missing_ok=True)
                sys.stdout.write(f"  ✗ calib {name}: capture failed — {exc}\n")
                sys.stdout.flush()
                continue
            sys.stdout.write(f"  ◇ calib {name}\n")
            sys.stdout.flush()
        _run_agent_browser(
            ["agent-browser", "--session", calib_session, "close"],
        )

    if confidence_map:
        suspects = sorted(
            name
            for name, sides in confidence_map.items()
            if any(not (c or {}).get("quiescent", True) for c in sides.values())
        )
        (section_dir / "capture-confidence.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    # Sections whose settle probe timed out mid-animation:
                    # downstream AE failures on these are "capture suspect",
                    # not necessarily impl errors.
                    "suspectSections": suspects,
                    "sections": confidence_map,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--print-settle":
        # H9: expose the derived settle for section-compare.sh (and tests).
        print(derive_settle_seconds(args[1]))
        return 0
    if len(args) == 1 and args[0] == "--print-cmp-selectors":
        # Single source of truth: section-compare.sh removes the same overlays at
        # the same stage, and a second hand-maintained copy drifts. Drift matters
        # because the ref-calib capture applies only _pause_js, so a selector
        # present in one path and not the other makes ref and ref-calib disagree.
        print(", ".join(CMP_OVERLAY_SELECTORS))
        return 0
    if len(args) != 1:
        print(
            "usage: python -m ui_clone.section_capture <matches.json> | "
            "--print-settle <transition-spec.json> | --print-cmp-selectors",
            file=sys.stderr,
        )
        return 2

    matches_raw = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    if not isinstance(matches_raw, list):
        print("matches.json must contain a list", file=sys.stderr)
        return 1
    matches = [row for row in matches_raw if isinstance(row, dict)]
    return capture_matched_sections(matches)


if __name__ == "__main__":
    raise SystemExit(main())
