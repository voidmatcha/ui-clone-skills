"""Bounded ``agent-browser`` subprocess primitives for section capture, plus
the viewport assertion, scroll-metrics, and live-section-rect probes built on
them.

Moved verbatim out of ``ui_clone.section_capture``; that module re-exports
every name here.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from ui_clone.pipeline_logs import _as_text
from ui_clone.section_capture_js import _live_section_rect_js, _scroll_metrics_js
from ui_clone.section_capture_primitives import _as_float, _is_number, _rect_from_capture

# Every `agent-browser` subprocess call below drives a real browser session
# over CDP; a wedged/hung browser (dead host, network partition, a page that
# never settles) would otherwise hang this call forever with no way for a
# caller (verify.py's own 600s gate timeout included) to distinguish "slow"
# from "dead". `magick` calls in this file operate on local files and are not
# in scope for this timeout — they don't share this failure mode.
_AGENT_BROWSER_TIMEOUT = 120


def _unwrap_eval_json(raw: str) -> dict[str, Any] | None:
    """Unwrap agent-browser's double-JSON-encoded eval output to a dict."""
    v: Any = raw.strip()
    for _ in range(4):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return None
        elif isinstance(v, dict):
            inner = v.get("data") if v.get("data") is not None else v.get("result")
            if inner is None:
                break
            v = inner
        else:
            break
    return v if isinstance(v, dict) else None


def _run_agent_browser(args: list[str]) -> subprocess.CompletedProcess[str]:
    """subprocess.run for an `agent-browser` CLI call, bounded so a wedged
    browser session (dead host, hung page) cannot hang capture forever."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=_AGENT_BROWSER_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            exc.cmd,
            returncode=124,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr) + f"\n[section_capture] agent-browser timed out after {exc.timeout}s\n",
        )


def _run_agent_eval(session: str, js: str) -> None:
    _run_agent_browser(["agent-browser", "--session", session, "eval", js])


def _run_agent_eval_text(session: str, js: str) -> str:
    result = _run_agent_browser(["agent-browser", "--session", session, "eval", js])
    return (result.stdout or "").strip()


def _ensure_viewport(
    session: str,
    expect_w: int,
    *,
    evaluator: Any = None,
    setter: Any = None,
    settle: float = 0.8,
) -> None:
    """V-1 (loop-nvti-4): the agent-browser session viewport silently REVERTS
    mid-session (specific regression confound; a 14-depth sweep ran at 1280x633 and had
    to be discarded). Assert innerWidth in-page immediately before every
    screenshot; on mismatch re-set the viewport ONCE and re-assert; a
    persistent mismatch aborts the capture — a wrong-viewport crop poisons
    every downstream verdict and must never be written silently."""
    ev = evaluator or _run_agent_eval_text
    def _width() -> int | None:
        raw = ev(session, "(() => window.innerWidth)()").strip().strip('"')
        # exact-match only: digit-harvesting would render an eval ERROR like
        # "os error 35" as innerWidth=35 in the abort message (fable review).
        return int(raw) if raw.isdigit() else None

    got = _width()
    if got == expect_w:
        return
    if setter is None:
        def setter(sess: str, w: int) -> None:  # pragma: no cover - thin wrapper
            _run_agent_browser(
                ["agent-browser", "--session", sess, "set", "viewport",
                 str(w), os.environ.get("SECTION_CAPTURE_VIEW_H") or "900"],
            )
    setter(session, expect_w)
    time.sleep(settle)
    got = _width()
    if got != expect_w:
        raise SystemExit(
            f"section_capture: viewport assertion failed on session "
            f"{session!r}: innerWidth={got} expected={expect_w} after one "
            f"re-set — aborting (V-1: a wrong-viewport crop poisons every "
            f"downstream verdict)"
        )


def _scroll_metrics(session: str, scroller_selector: str) -> dict[str, float] | None:
    raw = _run_agent_eval_text(session, _scroll_metrics_js(scroller_selector))
    data = _unwrap_eval_json(raw)
    if not isinstance(data, dict):
        return None
    out: dict[str, float] = {}
    for key in ("y", "vh", "sh"):
        try:
            out[key] = float(data.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return out


def _resolve_live_section_rect(
    session: str,
    identity: dict[str, object] | None,
    expected_top: float,
) -> dict[str, object] | None:
    if not identity:
        return None
    data = _unwrap_eval_json(
        _run_agent_eval_text(session, _live_section_rect_js(identity, expected_top))
    )
    if not isinstance(data, dict):
        return None
    width = _as_float(data.get("width"))
    height = _as_float(data.get("height"))
    if width <= 0 or height <= 0:
        return None
    result: dict[str, object] = {
        "top": _as_float(data.get("top")),
        "left": _as_float(data.get("left")),
        "width": width,
        "height": height,
        "documentTop": _as_float(data.get("documentTop")),
    }
    for key in ("bottomSticky", "hitVisible"):
        if isinstance(data.get(key), bool):
            result[key] = data[key]
    if isinstance(data.get("position"), str):
        result["position"] = data["position"]
    foreground_roi = data.get("foregroundRoi")
    if isinstance(foreground_roi, dict):
        roi = _rect_from_capture(foreground_roi)
        if roi is not None:
            result["foregroundRoi"] = roi
    for key in ("foregroundRectCount", "underlayCanvasCount"):
        if _is_number(data.get(key)):
            result[key] = int(_as_float(data[key]))
    if _is_number(data.get("hitVisibleSamples")):
        result["hitVisibleSamples"] = int(data["hitVisibleSamples"])
    if _is_number(data.get("stickyEndScrollY")):
        result["stickyEndScrollY"] = _as_float(data["stickyEndScrollY"])
    return result
