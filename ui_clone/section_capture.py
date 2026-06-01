"""Safe section screenshot capture helpers for section-compare.sh.

The shell wrapper delegates matched-section capture here so selector-derived
section names and scroller selectors are handled as data, not shell syntax.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def safe_section_name(raw: object, *, max_length: int = 80) -> str:
    """Return a filename-safe section name.

    Section names originate from reference DOM ids/classes. Treat them as
    untrusted display data: remove path traversal punctuation, shell metachars,
    whitespace, and quotes while preserving readable alphanumeric tokens.
    """
    text = str(raw or "")
    text = text.replace("\\", "_").replace("/", "_")
    text = _SAFE_NAME_RE.sub("_", text)
    text = _MULTI_UNDERSCORE_RE.sub("_", text).strip("._-")
    if not text:
        text = "section"
    return text[:max_length]


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _fmt_num(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _scroll_js(scroll_y: float, scroller_selector: str) -> str:
    y = _fmt_num(scroll_y)
    if scroller_selector == "__document__":
        return f"(() => {{ window.scrollTo(0, {y}); return {y}; }})()"

    selector_literal = json.dumps(scroller_selector)
    return (
        "(() => {"
        f"const w = document.querySelector({selector_literal});"
        f"if (!w) {{ window.scrollTo(0, {y}); return {y}; }}"
        f"w.scrollTop = {y};"
        "w.dispatchEvent(new Event('scroll'));"
        "return w.scrollTop;"
        "})()"
    )


def _pause_js() -> str:
    css = (
        "*, *::before, *::after { animation-play-state: paused !important; "
        "transition-duration: 0s !important; }"
        + os.environ.get("SECTION_CAPTURE_DYNAMIC_PAUSE_EXTRA", "")
    )
    css_literal = json.dumps(css)
    return (
        "(() => {"
        "const s = document.getElementById('__sc-pause__');"
        "if (!s) {"
        "const ns = document.createElement('style');"
        "ns.id = '__sc-pause__';"
        f"ns.textContent = {css_literal};"
        "document.head.appendChild(ns);"
        "}"
        "document.querySelectorAll('video').forEach(v => { try { v.pause(); v.autoplay = false; if (v.readyState >= 1) v.currentTime = 0; } catch(e){} });"
        "document.querySelectorAll('#iubenda-cs-banner, [id^=iubenda-], [class*=iubenda], [id^=onetrust-], [class*=onetrust], [id^=osano-], [class*=osano], [id^=cky-], [class*=cookieconsent]').forEach(el => el.remove());"
        "return 'paused';"
        "})()"
    )


def _finish_js() -> str:
    return r"""(() => { try { if (typeof document.getAnimations === "function") { document.getAnimations().forEach(a => { try { a.finish(); } catch(e){} }); } } catch(e){} try { var __ST = window.ScrollTrigger || window.__sc_st || (window.gsap && window.gsap.core && window.gsap.core.globals && window.gsap.core.globals().ScrollTrigger); if (__ST && typeof __ST.getAll === "function") { __ST.getAll().forEach(function(st){ try { if (st.animation && typeof st.animation.progress === "function") st.animation.progress(1, false); if (typeof st.disable === "function") st.disable(false, false); } catch(e){} }); } } catch(e){} try { var __gs = window.gsap || window.__sc_gsap; if (__gs && __gs.globalTimeline && typeof __gs.globalTimeline.getChildren === "function") { __gs.globalTimeline.getChildren(true, true, true).forEach(t => { try { if (typeof t.progress === "function") t.progress(1, false); } catch(e){} }); } } catch(e){} try { if (window.anime && Array.isArray(window.anime.running)) { window.anime.running.slice().forEach(a => { try { a.seek(a.duration); a.pause(); } catch(e){} }); } } catch(e){} try { if (window.lottie && typeof window.lottie.getRegisteredAnimations === "function") { window.lottie.getRegisteredAnimations().forEach(a => { try { const last = (typeof a.totalFrames === "number" ? a.totalFrames : 1) - 1; a.goToAndStop(Math.max(0, last), true); } catch(e){} }); } document.querySelectorAll("lottie-player, dotlottie-player").forEach(el => { try { if (typeof el.seek === "function") el.seek("100%"); if (typeof el.pause === "function") el.pause(); } catch(e){} }); } catch(e){} try { var snapped = 0; document.querySelectorAll("[style*=translate3d]").forEach(function(el){ try { var s = el.getAttribute("style") || ""; var m = s.match(/translate3d\(\s*(-?[0-9.]+)px\s*,\s*(-?[0-9.]+)px\s*,\s*0(?:px)?\s*\)/); if (!m) return; var ax = Math.abs(parseFloat(m[1])); var ay = Math.abs(parseFloat(m[2])); if (ax >= 10 || ay >= 10) return; var op = parseFloat(el.style.opacity || "1"); if (!Number.isFinite(op) || op < 0.95) return; el.style.transform = "translate3d(0px, 0px, 0px)"; if (op > 0.999) el.style.opacity = "1"; snapped++; } catch(e){} }); } catch(e){} return "finished"; })()"""


def _run_agent_eval(session: str, js: str) -> None:
    subprocess.run(
        ["agent-browser", "--session", session, "eval", js],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_screenshot(session: str, output_path: Path) -> None:
    subprocess.run(
        ["agent-browser", "--session", session, "screenshot", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_crop(image_path: Path, rect: dict[str, object], clip_top: float) -> None:
    crop_h = min(_as_float(rect.get("height")), 1800.0)
    width = _as_float(rect.get("width"))
    left = _as_float(rect.get("left"))
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
) -> None:
    top = _as_float(rect.get("top"))
    scroll_y = max(0.0, top - 50.0)
    clip_top = top - scroll_y

    _run_agent_eval(session, _scroll_js(scroll_y, scroller_selector))
    time.sleep(0.1)
    _run_agent_eval(session, pause_js)
    if not skip_finish:
        _run_agent_eval(session, finish_js)
    time.sleep(max(0.2, wait_scroll_settle))

    output_path = section_dir / side / f"{name}.png"
    _run_screenshot(session, output_path)
    _run_crop(output_path, rect, clip_top)


def capture_matched_sections(matches: list[dict[str, Any]]) -> int:
    section_dir = Path(os.environ["SECTION_CAPTURE_DIR"]) / "sections"
    session_ref = os.environ["SECTION_CAPTURE_SESSION_REF"]
    session_impl = os.environ["SECTION_CAPTURE_SESSION_IMPL"]
    ref_scroller = os.environ.get("SECTION_CAPTURE_REF_SCROLLER_SEL", "__document__")
    impl_scroller = os.environ.get("SECTION_CAPTURE_IMPL_SCROLLER_SEL", "__document__")
    reuse_frozen_ref = os.environ.get("SECTION_CAPTURE_REUSE_FROZEN_REF", "0") == "1"
    skip_finish = os.environ.get("SECTION_CAPTURE_SKIP_FINISH", "0") == "1"
    wait_scroll_settle = _as_float(os.environ.get("SECTION_CAPTURE_WAIT_SCROLL_SETTLE"), 0.5)
    pause_js = _pause_js()
    finish_js = _finish_js()

    for match in matches:
        name = safe_section_name(match.get("name"))
        ref = match.get("ref")
        impl = match.get("impl")

        if isinstance(ref, dict) and not reuse_frozen_ref:
            rect = ref.get("rect")
            if isinstance(rect, dict):
                _capture_one(
                    session=session_ref,
                    section_dir=section_dir,
                    side="ref",
                    name=name,
                    rect=rect,
                    scroller_selector=ref_scroller,
                    pause_js=pause_js,
                    finish_js=finish_js,
                    skip_finish=skip_finish,
                    wait_scroll_settle=wait_scroll_settle,
                )

        if isinstance(impl, dict):
            rect = impl.get("rect")
            if isinstance(rect, dict):
                _capture_one(
                    session=session_impl,
                    section_dir=section_dir,
                    side="impl",
                    name=name,
                    rect=rect,
                    scroller_selector=impl_scroller,
                    pause_js=pause_js,
                    finish_js=finish_js,
                    skip_finish=skip_finish,
                    wait_scroll_settle=wait_scroll_settle,
                )

        sys.stdout.write(f"  ✓ {name}\n")
        sys.stdout.flush()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m ui_clone.section_capture <matches.json>", file=sys.stderr)
        return 2

    matches_raw = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    if not isinstance(matches_raw, list):
        print("matches.json must contain a list", file=sys.stderr)
        return 1
    matches = [row for row in matches_raw if isinstance(row, dict)]
    return capture_matched_sections(matches)


if __name__ == "__main__":
    raise SystemExit(main())
