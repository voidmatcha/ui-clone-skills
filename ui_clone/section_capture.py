"""Safe section screenshot capture helpers for section-compare.sh.

The shell wrapper delegates matched-section capture here so selector-derived
section names and scroller selectors are handled as data, not shell syntax.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ui_clone.pipeline_logs import _as_text

if TYPE_CHECKING:
    from typing import TypeGuard

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

# Every `agent-browser` subprocess call below drives a real browser session
# over CDP; a wedged/hung browser (dead host, network partition, a page that
# never settles) would otherwise hang this call forever with no way for a
# caller (verify.py's own 600s gate timeout included) to distinguish "slow"
# from "dead". `magick` calls in this file operate on local files and are not
# in scope for this timeout — they don't share this failure mode.
_AGENT_BROWSER_TIMEOUT = 120


def _is_number(value: object) -> TypeGuard[int | float]:
    """Keep shell entrypoints compatible with macOS system Python 3.9."""
    return isinstance(value, int) or isinstance(value, float)


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
    # Coerce first: callers may pass an int (e.g. forced_scroll_y) and
    # int.is_integer() only exists on Python 3.12+.
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _scroll_js(scroll_y: float, scroller_selector: str) -> str:
    y = _fmt_num(scroll_y)
    if scroller_selector == "__document__":
        return f"(() => {{ window.scrollTo(0, {y}); document.documentElement.setAttribute('data-section-compare-scrolled', ({y} > 0 ? '1' : '0')); return {y}; }})()"

    selector_literal = json.dumps(scroller_selector)
    return (
        "(() => {"
        f"const w = document.querySelector({selector_literal});"
        f"if (!w) {{ window.scrollTo(0, {y}); document.documentElement.setAttribute('data-section-compare-scrolled', ({y} > 0 ? '1' : '0')); return {y}; }}"
        f"w.scrollTop = {y};"
        "w.dispatchEvent(new Event('scroll'));"
        "return w.scrollTop;"
        "})()"
    )


def _disable_smooth_scroll_js() -> str:
    """Neutralize Lenis/smooth-scroll so a forced `scrollTo` actually sticks.

    A Lenis/Framer smooth-scroll controller intercepts the native scroll and
    animates back toward its own target during settle, collapsing the capture's
    forced_scroll_y to actualY=0 (specific regression: scrubbed sections all crop the start
    frame). This sets a capture flag the generator can honor, forces
    scroll-behavior:auto, and best-effort stops/destroys a live Lenis instance.
    Idempotent (guarded by a marker), so it is safe to call before every shot.
    """
    return (
        "(() => {"
        "try { window.__UI_CLONE_CAPTURE__ = true; } catch(e){}"
        "if (!document.getElementById('__sc-smooth-off__')) {"
        "const ns = document.createElement('style');"
        "ns.id = '__sc-smooth-off__';"
        "ns.textContent = 'html,body{scroll-behavior:auto !important;}';"
        "document.head.appendChild(ns);"
        "}"
        "try {"
        "['lenis','__lenis','Lenis','smoothScroll','__smoothScroll'].forEach(k => {"
        "const o = window[k];"
        "if (o && typeof o === 'object') { try { o.stop && o.stop(); } catch(e){} try { o.destroy && o.destroy(); } catch(e){} }"
        "});"
        "} catch(e){}"
        "return 'smooth-off';"
        "})()"
    )


def _reference_runtime_normalization_js() -> str:
    """Reapply opt-in reference state that a reload or page script can undo.

    The shell wrapper applies these controls after opening the reference page,
    but section capture can run in a separate calibration session and dynamic
    pages can restore their inline scroll cap while the capture loop is active.
    Keeping the normalization reference-only preserves the implementation as
    the thing under test.
    """
    cap_selector = (os.environ.get("REF_SCROLL_CAP_SELECTOR") or "").strip()
    reset_selector = (
        os.environ.get("REF_RESET_SCROLLLEFT_SELECTOR") or ""
    ).strip()
    if not cap_selector and not reset_selector:
        return "undefined"
    return (
        "(() => {"
        f"const capSelector = {json.dumps(cap_selector)};"
        f"const resetSelector = {json.dumps(reset_selector)};"
        "let capCount = 0; let resetCount = 0;"
        "if (capSelector) { try {"
        "const nodes = document.querySelectorAll(capSelector);"
        "nodes.forEach(el => el.style.setProperty('max-height','none','important'));"
        "capCount = nodes.length;"
        "} catch (_error) {} }"
        "if (resetSelector) { try {"
        "const nodes = document.querySelectorAll(resetSelector);"
        "nodes.forEach(el => { el.scrollLeft = 0; });"
        "resetCount = nodes.length;"
        "} catch (_error) {} }"
        "let residualCap = 0;"
        "if (capSelector) { try {"
        "residualCap = Array.from(document.querySelectorAll(capSelector)).filter(el => "
        "el.style.getPropertyValue('max-height') !== 'none').length;"
        "} catch (_error) { residualCap = -1; } }"
        "return JSON.stringify({capCount, resetCount, residualCap, height: document.documentElement.scrollHeight});"
        "})()"
    )


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



def _fixed_overlay_toggle_js(active: bool) -> str:
    selectors = (
        os.environ.get("SECTION_CAPTURE_FIXED_OVERLAY_SELECTORS")
        or os.environ.get("SECTION_FIXED_OVERLAY_SELECTORS")
        or ""
    ).strip()
    if not selectors:
        return "undefined"
    css = f"{selectors} {{ visibility: hidden !important; }}"
    if not active:
        return """
(() => {
  const old = document.getElementById("__section_compare_fixed_overlay_mask");
  if (old) old.remove();
})()
"""
    return f"""
(() => {{
  const old = document.getElementById("__section_compare_fixed_overlay_mask");
  if (old) old.remove();
  const style = document.createElement("style");
  style.id = "__section_compare_fixed_overlay_mask";
  style.textContent = {json.dumps(css)};
  document.head.appendChild(style);
}})()
"""


def _canvas_underlay_js() -> str:
    """Replace canvas pixels with a deterministic layer without changing stacking.

    The black background fills transparent canvas pixels; brightness(0) makes
    opaque pixels black. Keeping the element in its original stacking context
    preserves DOM foreground painted above it. This is enabled only by the
    section comparator and is applied symmetrically to ref and impl.
    """
    if os.environ.get("SECTION_CAPTURE_CANVAS_UNDERLAY") != "1":
        return "undefined"
    return """
(() => {
  let style = document.getElementById("__sc-canvas-underlay__");
  if (!style) {
    style = document.createElement("style");
    style.id = "__sc-canvas-underlay__";
    style.textContent = "canvas { visibility: visible !important; background: #202020 !important; filter: brightness(0) !important; opacity: 1 !important; mix-blend-mode: normal !important; }";
    document.head.appendChild(style);
  }
  return document.querySelectorAll("canvas").length;
})()
"""

# Consent/privacy (CMP) overlay containers removed during the capture settle.
#
# A persistent CMP banner occludes content and inflates EVERY section's AE
# uniformly. Removal is applied identically to the reference and to the
# implementation, so it cannot favour a faithful or a broken clone.
#
# Every entry must be a vendor-namespaced container id/class. A substring match
# on a generic word is banned: it deletes the page's own content and yields a
# doctored reference. Two such entries were removed after being measured --
#   [class*=cookieconsent]  Cookiebot's uc.js sets cookieconsent-optin-marketing
#                           on consent-gated iframes and their containers, so
#                           this stripped real embeds; meanwhile Osano/Insites
#                           cookieconsent -- its intended target -- never puts
#                           that string in a class, so it covered nothing.
#                           Replaced by .cc-window.
#   [id^=cky-]              On a CookieYes frontend the only cky- ids are
#                           <style id="cky-style"> and cky-style-inline, so this
#                           stripped the banner's stylesheet and left the banner
#                           reflowing as unstyled block text. The containers are
#                           classes. Replaced by the .cky-* trio.
# Per-site needs belong in SECTION_FIXED_OVERLAY_SELECTORS, not here.
CMP_OVERLAY_SELECTORS: tuple[str, ...] = (
    # iubenda. The CMP core (cookie_solution/iubenda_cs core-<lang>.js) reaches
    # for exactly three ids, so a [id^=iubenda-] prefix bought nothing and did
    # catch the badge script's id="iubenda-embed" fallback. Its overlay roots are
    # NOT all under -cs- (iubenda-alert-dialog, iubenda-iframe-popup,
    # iubenda-floatable-*), so the class match stays broad; the two exclusions are
    # the badge anchor the SITE renders in its own footer
    # (class="iubenda-white iubenda-embed"), which is page content and must
    # survive into the reference so the clone is held to reproducing it. Verified:
    # those two classes appear 0 times in the CMP core.
    "#iubenda-cs-banner",
    "#iubenda-iframe-popup",
    "#iubenda_cs_rejection_recovery_popup",
    "[class*=iubenda]:not(.iubenda-embed):not(.iubenda-ibadge)",
    # OneTrust
    "[id^=onetrust-]",
    "[class*=onetrust]",
    # Osano
    "[id^=osano-]",
    "[class*=osano]",
    # Osano / Insites cookieconsent
    ".cc-window",
    # CookieYes
    ".cky-consent-container",
    ".cky-overlay",
    ".cky-modal",
    # Cookiebot
    "#CybotCookiebotDialog",
    "#CybotCookiebotDialogBodyUnderlay",
    "#CookiebotWidget",
    # Usercentrics
    "#usercentrics-root",
    "#usercentrics-cmp-ui",
    # Didomi
    "#didomi-host",
    # Quantcast Choice
    ".qc-cmp2-container",
    # Complianz
    "#cmplz-cookiebanner-container",
)


def _pause_js() -> str:
    css = (
        "*, *::before, *::after { animation-play-state: paused !important; "
        "transition-duration: 0s !important; }"
        + os.environ.get("SECTION_CAPTURE_DYNAMIC_PAUSE_EXTRA", "")
    )
    css_literal = json.dumps(css)
    cmp_literal = json.dumps(", ".join(CMP_OVERLAY_SELECTORS))
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
        # try/catch: querySelectorAll throws on a malformed list, which would
        # abort the IIFE before `return 'paused'` and silently skip the pause.
        f"try {{ document.querySelectorAll({cmp_literal}).forEach(el => el.remove()); }} catch (e) {{}}"
        "return 'paused';"
        "})()"
    )


def _finish_js() -> str:
    """Fast-forward every animation engine to its end frame before a shot.

    The trailing translate3d block snaps near-settled framer transforms to
    identity. It must only NORMALIZE an opacity the element already declares
    inline (0.9995 -> 1) — writing one where the element had none overrides the
    stylesheet and force-shows scroll-gated reveals that are legitimately
    hidden at the capture anchor (one observed site's pyramid `.food`: 63k AE of pure
    capture artifact). See tests/test_section_capture_finish_opacity.py.
    """
    return r"""(() => { try { if (typeof document.getAnimations === "function") { document.getAnimations().forEach(a => { try { a.finish(); } catch(e){} }); } } catch(e){} try { var __ST = window.ScrollTrigger || window.__sc_st || (window.gsap && window.gsap.core && window.gsap.core.globals && window.gsap.core.globals().ScrollTrigger); if (__ST && typeof __ST.getAll === "function") { __ST.getAll().forEach(function(st){ try { if (st.animation && typeof st.animation.progress === "function") st.animation.progress(1, false); if (typeof st.disable === "function") st.disable(false, false); } catch(e){} }); } } catch(e){} try { var __gs = window.gsap || window.__sc_gsap; if (__gs && __gs.globalTimeline && typeof __gs.globalTimeline.getChildren === "function") { __gs.globalTimeline.getChildren(true, true, true).forEach(t => { try { if (typeof t.progress === "function") t.progress(1, false); } catch(e){} }); } } catch(e){} try { if (window.anime && Array.isArray(window.anime.running)) { window.anime.running.slice().forEach(a => { try { a.seek(a.duration); a.pause(); } catch(e){} }); } } catch(e){} try { if (window.lottie && typeof window.lottie.getRegisteredAnimations === "function") { window.lottie.getRegisteredAnimations().forEach(a => { try { const last = (typeof a.totalFrames === "number" ? a.totalFrames : 1) - 1; a.goToAndStop(Math.max(0, last), true); } catch(e){} }); } document.querySelectorAll("lottie-player, dotlottie-player").forEach(el => { try { if (typeof el.seek === "function") el.seek("100%"); if (typeof el.pause === "function") el.pause(); } catch(e){} }); } catch(e){} try { var snapped = 0; document.querySelectorAll("[style*=translate3d]").forEach(function(el){ try { var s = el.getAttribute("style") || ""; var re = /translate3d\(\s*(-?[0-9.]+)px\s*,\s*(-?[0-9.]+)px\s*,\s*0(?:px)?\s*\)/; var transformSource = (el.style.transform || "").trim(); if (!transformSource) { var tm = s.match(/(?:^|;)\s*transform\s*:\s*([^;]+)/i); transformSource = tm ? tm[1].trim() : ""; } var m = (transformSource || s).match(re); if (!m) return; var ax = Math.abs(parseFloat(m[1])); var ay = Math.abs(parseFloat(m[2])); if (ax >= 10 || ay >= 10) return; var rawOp = (el.style.opacity || "").trim(); var op = parseFloat(rawOp === "" ? "1" : rawOp); if (!Number.isFinite(op) || op < 0.95) return; el.style.transform = (transformSource || m[0]).replace(re, "translate3d(0px, 0px, 0px)"); if (rawOp !== "" && op > 0.999) el.style.opacity = "1"; snapped++; } catch(e){} }); } catch(e){} return "finished"; })()"""


def _settle_js() -> str:
    """Post-finish settle probe for engines _finish_js cannot fast-forward.

    Framer Motion drives animations through a private rAF frameloop —
    document.getAnimations() never sees them, so the WAAPI/GSAP/anime/Lottie
    fast-forward leaves Framer (and IntersectionObserver-started) animations
    mid-flight, and a deterministic capture of that frozen frame is
    deterministically WRONG (specific regression: 60/76 reveals frozen). This probe
    (1) best-effort enables MotionGlobalConfig.skipAnimations, (2) yields two
    rAF ticks so pending IO callbacks run, then (3) polls an inline-style
    fingerprint until two consecutive samples are identical (quiescent) or
    the budget runs out — and reports the verdict so the capture carries a
    machine-readable confidence instead of silently penalizing the impl.
    """
    return r"""(async () => {
  try { if (window.MotionGlobalConfig) window.MotionGlobalConfig.skipAnimations = true; } catch(e){}
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  await raf(); await raf();
  const fp = () => {
    let s = "";
    try {
      const els = document.querySelectorAll('[style*="transform"],[style*="opacity"]');
      let n = 0;
      for (const el of els) { s += (el.getAttribute("style") || "") + ";"; if (++n >= 200) break; }
    } catch(e){}
    let running = 0;
    try { running = document.getAnimations().filter(a => a.playState === "running").length; } catch(e){}
    return s + "|" + running;
  };
  let prev = fp(); let rounds = 0; let quiescent = false;
  for (; rounds < 8; rounds++) {
    await wait(120); await raf();
    const cur = fp();
    if (cur === prev) { quiescent = true; break; }
    prev = cur;
  }
  let running = 0;
  try { running = document.getAnimations().filter(a => a.playState === "running").length; } catch(e){}
  return JSON.stringify({ quiescent: quiescent, rounds: rounds, runningAnimations: running });
})()"""


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


def _duration_to_seconds(dur: object) -> float | None:
    """Coerce a spec duration to seconds. Accepts a number (already seconds) or
    a CSS duration string ('1200ms', '1s', '0.8s') or a bare numeric string
    (seconds). Returns None when unparseable. transition-spec-extract emits
    ms/s strings while transition-spec-rules.md documents bare seconds — both
    must parse, or the derived settle silently falls back to the 0.5s floor and
    reference sections get captured mid-transition (codex P2 / extract H1)."""
    if isinstance(dur, bool):
        return None
    if _is_number(dur):
        return float(dur)
    if not isinstance(dur, str):
        return None
    s = dur.strip().lower()
    if not s:
        return None
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        if s.endswith("s"):
            return float(s[:-1])
        return float(s)  # bare numeric string -> seconds
    except ValueError:
        return None


def derive_settle_seconds(spec_path: Path | str) -> float:
    """H9 (loop-nvti-3/4): the fixed 0.5s settle captured choreography-alive
    reference pages MID-TRANSITION — transient ref crops overturned two
    eyeball observations before being identified. Derive the settle from the
    spec itself: rest-reeval margin (0.4s) + the longest declared transition
    duration, floor 0.5s, cap 4.0s. Absent/unparseable spec keeps the legacy
    0.5s (no behavior change for non-choreography sites, per the fable
    constraint that the value must be derived, never site-tuned)."""
    margin, floor, cap = 0.4, 0.5, 4.0
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return floor
    entries = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(entries, list):
        return floor
    longest = 0.0
    for e in entries:
        if not isinstance(e, dict):
            continue
        anim = e.get("animation")
        dur = anim.get("duration") if isinstance(anim, dict) else None
        if dur is None:
            continue
        secs = _duration_to_seconds(dur)
        if secs is None:
            continue
        longest = max(longest, secs)
    if longest <= 0.0:
        return floor
    return round(min(cap, max(floor, margin + longest)), 3)


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


def should_pin_to_bottom(
    *,
    top: float,
    height: float,
    scroll_height: float,
    viewport_h: float,
    factor: float = 1.5,
) -> bool:
    """True when the section's bottom sits within `factor` viewports of the
    page end.

    Near-end sections must be captured with the page pinned to maxScroll:
    (1) `window.scrollTo(top - 50)` silently clamps there anyway, so the
    legacy fixed `clip_top = 50` assumption cropped the wrong band, and
    (2) end-of-page reveal latches only mount content once the page is
    actually scrolled to the end (observed: a footer whose content never
    rendered inside the capture window, producing 2-color background-only
    crops on both sides and an AE=0 vacuous pass).

    A section whose own height already spans most of the document (a
    coarse single-section match on an impl without ref's granular markup)
    makes `top + height` land near `scroll_height` no matter where the
    section actually starts, so the heuristic above misfires and pins a
    whole-page section to maxScroll — scrolling past all real content into
    blank territory. Real footers/near-bottom elements never approach half
    the document height, so excluding that case only removes the
    degenerate whole-page-as-one-section match, not a legitimate near-end
    element. Tradeoff: on a short page (e.g. a 2-viewport landing page) a
    genuinely final, full-viewport-height section can legitimately reach
    this 50% threshold and lose pinning, cropping its last ~viewport_h/2 of
    content instead of the true bottom. Symmetric across ref/impl (both
    captured identically), so it doesn't corrupt the AE verdict — just a
    known imprecision on short pages, not addressed here.
    """
    if viewport_h <= 0:
        return False
    if scroll_height > 0 and height >= scroll_height * 0.5:
        return False
    return top + height >= scroll_height - factor * viewport_h


def desired_scroll_y(
    *,
    top: float,
    height: float,
    scroll_height: float,
    viewport_h: float,
    factor: float = 1.5,
) -> float:
    if should_pin_to_bottom(
        top=top, height=height, scroll_height=scroll_height,
        viewport_h=viewport_h, factor=factor,
    ):
        return max(0.0, scroll_height - viewport_h)
    return max(0.0, top - 50.0)


def _scroll_metrics_js(scroller_selector: str) -> str:
    if scroller_selector == "__document__":
        return (
            "(() => JSON.stringify({y: window.scrollY, vh: window.innerHeight,"
            " sh: document.documentElement.scrollHeight}))()"
        )
    selector_literal = json.dumps(scroller_selector)
    return (
        "(() => {"
        f"const w = document.querySelector({selector_literal});"
        "if (!w) return JSON.stringify({y: window.scrollY, vh: window.innerHeight,"
        " sh: document.documentElement.scrollHeight});"
        "return JSON.stringify({y: w.scrollTop, vh: w.clientHeight, sh: w.scrollHeight});"
        "})()"
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


def crop_unique_colors(image_path: Path) -> int | None:
    proc = subprocess.run(
        ["magick", "identify", "-format", "%k", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _crop_is_blank(image_path: Path, *, min_std: float = 0.05) -> bool:
    """True when a crop carries no real content: an off-canvas 1x1 stub, or a
    near-uniform band (std below min_std) — the blank-ref capture-failure class
    a pinned tall section hits when maxScroll scrolls past its top content."""
    proc = subprocess.run(
        ["magick", "identify", "-format", "%w %h %[fx:standard_deviation]", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        parts = proc.stdout.strip().split()
        w, h, std = int(parts[0]), int(parts[1]), float(parts[2])
    except (ValueError, IndexError):
        return False
    if w <= 2 and h <= 2:
        return True
    return std < min_std


def crop_is_off_canvas(*, clip_top: float, crop_h: float, canvas_h: float) -> bool:
    """True when the crop rect has zero intersection with the screenshot.

    Off-canvas rects happen legitimately: a settled intro overlay parked at
    page rect -900..0 (end-to-end run). ImageMagick's out-of-bounds crop output
    then depends on the source PNG's alpha channel — transparent on the
    alpha-bearing ref capture, a clamped edge pixel on a no-alpha impl
    screenshot — which guarantees a saturating 1px AE diff that no impl
    change can fix.
    """
    return clip_top + crop_h <= 0 or clip_top >= canvas_h


def write_transparent_stub(image_path: Path) -> None:
    """Deterministic 1x1 fully-transparent RGBA crop for off-canvas rects."""
    subprocess.run(
        ["magick", "-size", "1x1", "xc:none", f"PNG32:{image_path}"],
        capture_output=True,
        text=True,
        check=False,
    )


def _canvas_height(image_path: Path) -> float:
    proc = subprocess.run(
        ["magick", "identify", "-format", "%h", str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


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


def _live_section_rect_js(identity: dict[str, object], expected_top: float) -> str:
    payload = json.dumps(
        {
            "id": identity.get("id") or identity.get("elementId"),
            "tag": identity.get("tag") or identity.get("tagName"),
            "className": identity.get("className") or identity.get("classes"),
            "text": identity.get("text") or identity.get("fingerprint") or identity.get("name"),
            "selector": identity.get("selector"),
            "expectedTop": expected_top,
        }
    )
    canvas_underlay = os.environ.get("SECTION_CAPTURE_CANVAS_UNDERLAY") == "1"
    return f"""(() => {{
  const identity = {payload};
  const norm = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const tag = norm(identity.tag).toLowerCase();
  const id = norm(identity.id);
  const classes = norm(identity.className).split(" ").filter(Boolean);
  const needle = norm(identity.text).toLowerCase().slice(0, 160);
  const selector = tag ? tag : "*";
  const nodes = Array.from(document.querySelectorAll(selector));
  let selectedNodes = [];
  if (identity.selector) {{
    try {{ selectedNodes = Array.from(document.querySelectorAll(identity.selector)); }} catch (_error) {{}}
  }}
  const uniqueSemanticTag = new Set(["main", "header", "footer", "nav", "article"]);
  const uniqueSemanticMatch = uniqueSemanticTag.has(tag) && nodes.length === 1;
  const candidates = [];
  for (const node of nodes) {{
    const rect = node.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    const nodeId = norm(node.id);
    const classList = Array.from(node.classList || []);
    const idMatch = !!id && nodeId === id;
    const classMatch = classes.length > 0 && classes.every((cls) =>
      classList.includes(cls) || (
        cls.length >= 6 && classList.some((actual) => actual.startsWith(cls))
      )
    );
    const textMatch = !!needle && norm(node.textContent).toLowerCase().includes(needle);
    const selectorMatch = selectedNodes.includes(node);
    if (!selectorMatch && !idMatch && !classMatch && !textMatch && !uniqueSemanticMatch) continue;
    let score = 0;
    if (selectorMatch) score += 200;
    if (idMatch) score += 100;
    if (classMatch) score += 50 + classes.length;
    if (textMatch) score += 20;
    if (tag && node.tagName.toLowerCase() === tag) score += 5;
    if (uniqueSemanticMatch) score += 1;
    const nodePosition = getComputedStyle(node).position;
    let bottomSticky = false;
    let stickyEndScrollY = null;
    for (let owner = node; owner && owner !== document.documentElement; owner = owner.parentElement) {{
      const ownerStyle = getComputedStyle(owner);
      if (ownerStyle.position === "sticky" && ownerStyle.bottom !== "auto") {{
        bottomSticky = true;
        const ownerRect = owner.getBoundingClientRect();
        const containingBlock = owner.parentElement;
        const containingRect = containingBlock && containingBlock.getBoundingClientRect();
        const stuckToViewportBottom = Math.abs(ownerRect.bottom - window.innerHeight) <= 1;
        if (containingRect && stuckToViewportBottom) {{
          const stickyEndDocumentTop = containingRect.bottom + window.scrollY - ownerRect.height;
          const targetY = stickyEndDocumentTop - ownerRect.top;
          if (targetY > window.scrollY + 1) stickyEndScrollY = targetY;
        }}
        break;
      }}
    }}
    const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const samplePoints = [0.1, 0.3, 0.5, 0.7, 0.9].map((ratio) => [
      x,
      Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height * ratio))
    ]);
    const hitVisibleSamples = samplePoints.filter(([sampleX, sampleY]) =>
      document.elementsFromPoint(sampleX, sampleY).some(
        (hit) => hit === node || node.contains(hit)
      )
    ).length;
    const hitVisible = hitVisibleSamples === samplePoints.length;
    const documentTop = rect.top + window.scrollY;
    candidates.push({{
      node,
      top: rect.top,
      left: rect.left,
      width: rect.width,
      height: rect.height,
      documentTop,
      bottomSticky,
      stickyEndScrollY,
      hitVisible,
      hitVisibleSamples,
      position: nodePosition,
      score,
      distance: Math.abs(documentTop - Number(identity.expectedTop || 0))
    }});
  }}
  candidates.sort((a, b) => (b.score - a.score) || (a.distance - b.distance));
  const best = candidates[0] || null;
  if (best) {{
    const bestNode = best.node;
    delete best.node;
    if ({str(canvas_underlay).lower()}) {{
      const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      const sectionRect = bestNode.getBoundingClientRect();
      const canvases = Array.from(document.querySelectorAll("canvas")).filter((canvas) => {{
        const style = getComputedStyle(canvas);
        const rect = canvas.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 && overlaps(rect, sectionRect);
      }});
      const foregroundRects = [];
      const walker = document.createTreeWalker(bestNode, NodeFilter.SHOW_TEXT);
      for (let textNode = walker.nextNode(); textNode; textNode = walker.nextNode()) {{
        if (!norm(textNode.nodeValue)) continue;
        const owner = textNode.parentElement;
        if (!owner || owner.closest("canvas, video, iframe")) continue;
        const style = getComputedStyle(owner);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) <= 0) continue;
        const range = document.createRange();
        range.selectNodeContents(textNode);
        for (const rect of range.getClientRects()) {{
          if (rect.width <= 1 || rect.height <= 1 || !overlaps(rect, sectionRect)) continue;
          const left = Math.max(0, rect.left, sectionRect.left);
          const top = Math.max(0, rect.top, sectionRect.top);
          const right = Math.min(window.innerWidth, rect.right, sectionRect.right);
          const bottom = Math.min(window.innerHeight, rect.bottom, sectionRect.bottom);
          if (right <= left || bottom <= top) continue;
          const x = (left + right) / 2;
          const y = (top + bottom) / 2;
          const hit = document.elementFromPoint(x, y);
          const foregroundHit = !!hit && (owner === hit || owner.contains(hit) || hit.contains(owner));
          const canvasBehind = canvases.some((canvas) => {{
            const canvasRect = canvas.getBoundingClientRect();
            return x >= canvasRect.left && x <= canvasRect.right && y >= canvasRect.top && y <= canvasRect.bottom;
          }});
          if (foregroundHit && canvasBehind) foregroundRects.push({{left, top, right, bottom}});
        }}
      }}
      if (foregroundRects.length) {{
        const pad = 8;
        const left = Math.max(0, sectionRect.left, Math.min(...foregroundRects.map(r => r.left)) - pad);
        const top = Math.max(0, sectionRect.top, Math.min(...foregroundRects.map(r => r.top)) - pad);
        const right = Math.min(window.innerWidth, sectionRect.right, Math.max(...foregroundRects.map(r => r.right)) + pad);
        const bottom = Math.min(window.innerHeight, sectionRect.bottom, Math.max(...foregroundRects.map(r => r.bottom)) + pad);
        if (right > left && bottom > top) {{
          best.foregroundRoi = {{left, top, width: right - left, height: bottom - top}};
          best.foregroundRectCount = foregroundRects.length;
          best.underlayCanvasCount = canvases.length;
        }}
      }}
    }}
  }}
  return JSON.stringify(best);
}})()"""


def _rect_from_capture(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    rect = {
        "top": _as_float(raw.get("top")),
        "left": _as_float(raw.get("left")),
        "width": _as_float(raw.get("width")),
        "height": _as_float(raw.get("height")),
    }
    if rect["width"] <= 0 or rect["height"] <= 0:
        return None
    return rect


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
