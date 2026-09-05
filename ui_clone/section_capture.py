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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TypeGuard

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


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
    hidden at the capture anchor (realfood pyramid `.food`: 63k AE of pure
    capture artifact). See tests/test_section_capture_finish_opacity.py.
    """
    return r"""(() => { try { if (typeof document.getAnimations === "function") { document.getAnimations().forEach(a => { try { a.finish(); } catch(e){} }); } } catch(e){} try { var __ST = window.ScrollTrigger || window.__sc_st || (window.gsap && window.gsap.core && window.gsap.core.globals && window.gsap.core.globals().ScrollTrigger); if (__ST && typeof __ST.getAll === "function") { __ST.getAll().forEach(function(st){ try { if (st.animation && typeof st.animation.progress === "function") st.animation.progress(1, false); if (typeof st.disable === "function") st.disable(false, false); } catch(e){} }); } } catch(e){} try { var __gs = window.gsap || window.__sc_gsap; if (__gs && __gs.globalTimeline && typeof __gs.globalTimeline.getChildren === "function") { __gs.globalTimeline.getChildren(true, true, true).forEach(t => { try { if (typeof t.progress === "function") t.progress(1, false); } catch(e){} }); } } catch(e){} try { if (window.anime && Array.isArray(window.anime.running)) { window.anime.running.slice().forEach(a => { try { a.seek(a.duration); a.pause(); } catch(e){} }); } } catch(e){} try { if (window.lottie && typeof window.lottie.getRegisteredAnimations === "function") { window.lottie.getRegisteredAnimations().forEach(a => { try { const last = (typeof a.totalFrames === "number" ? a.totalFrames : 1) - 1; a.goToAndStop(Math.max(0, last), true); } catch(e){} }); } document.querySelectorAll("lottie-player, dotlottie-player").forEach(el => { try { if (typeof el.seek === "function") el.seek("100%"); if (typeof el.pause === "function") el.pause(); } catch(e){} }); } catch(e){} try { var snapped = 0; document.querySelectorAll("[style*=translate3d]").forEach(function(el){ try { var s = el.getAttribute("style") || ""; var m = s.match(/translate3d\(\s*(-?[0-9.]+)px\s*,\s*(-?[0-9.]+)px\s*,\s*0(?:px)?\s*\)/); if (!m) return; var ax = Math.abs(parseFloat(m[1])); var ay = Math.abs(parseFloat(m[2])); if (ax >= 10 || ay >= 10) return; var rawOp = (el.style.opacity || "").trim(); var op = parseFloat(rawOp === "" ? "1" : rawOp); if (!Number.isFinite(op) || op < 0.95) return; el.style.transform = "translate3d(0px, 0px, 0px)"; if (rawOp !== "" && op > 0.999) el.style.opacity = "1"; snapped++; } catch(e){} }); } catch(e){} return "finished"; })()"""


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


def _run_agent_eval(session: str, js: str) -> None:
    subprocess.run(
        ["agent-browser", "--session", session, "eval", js],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_agent_eval_text(session: str, js: str) -> str:
    result = subprocess.run(
        ["agent-browser", "--session", session, "eval", js],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or "").strip()


def _run_screenshot(session: str, output_path: Path) -> None:
    subprocess.run(
        ["agent-browser", "--session", session, "screenshot", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )


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
            subprocess.run(
                ["agent-browser", "--session", sess, "set", "viewport",
                 str(w), os.environ.get("SECTION_CAPTURE_VIEW_H") or "900"],
                capture_output=True, text=True, check=False,
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
    """
    if viewport_h <= 0:
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
    page rect -900..0 (loop-e2e-4). ImageMagick's out-of-bounds crop output
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
    # while its content sits in the visible band below — the realfood "Eat Real
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
            "expectedTop": expected_top,
        }
    )
    return f"""(() => {{
  const identity = {payload};
  const norm = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const tag = norm(identity.tag).toLowerCase();
  const id = norm(identity.id);
  const classes = norm(identity.className).split(" ").filter(Boolean);
  const needle = norm(identity.text).toLowerCase().slice(0, 160);
  const selector = tag ? tag : "*";
  const nodes = Array.from(document.querySelectorAll(selector));
  const candidates = [];
  for (const node of nodes) {{
    const rect = node.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    const nodeId = norm(node.id);
    const classList = Array.from(node.classList || []);
    const idMatch = !!id && nodeId === id;
    const classMatch = classes.length > 0 && classes.every((cls) => classList.includes(cls));
    const textMatch = !!needle && norm(node.textContent).toLowerCase().includes(needle);
    if (!idMatch && !classMatch && !textMatch) continue;
    let score = 0;
    if (idMatch) score += 100;
    if (classMatch) score += 50 + classes.length;
    if (textMatch) score += 20;
    if (tag && node.tagName.toLowerCase() === tag) score += 5;
    const documentTop = rect.top + window.scrollY;
    candidates.push({{
      top: rect.top,
      left: rect.left,
      width: rect.width,
      height: rect.height,
      documentTop,
      score,
      distance: Math.abs(documentTop - Number(identity.expectedTop || 0))
    }});
  }}
  candidates.sort((a, b) => (b.score - a.score) || (a.distance - b.distance));
  const best = candidates[0] || null;
  return JSON.stringify(best);
}})()"""


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
    return {
        "top": _as_float(data.get("top")),
        "left": _as_float(data.get("left")),
        "width": width,
        "height": height,
        "documentTop": _as_float(data.get("documentTop")),
    }


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
) -> dict[str, Any] | None:
    top = _as_float(rect.get("top"))
    height = _as_float(rect.get("height"))

    # Plan the scroll from real page metrics. Near-end sections pin to
    # maxScroll (the request would clamp there anyway, and end-of-page
    # reveal latches only mount once the page is actually at the end).
    factor = _as_float(os.environ.get("SECTION_CAPTURE_BOTTOM_ANCHOR_FACTOR"), 1.5)
    metrics = _scroll_metrics(session, scroller_selector)
    if forced_scroll_y is not None:
        # batch-13 ITEM 1 — CAPTURE DETERMINISM. Reuse the EXACT scroll position
        # the frozen-ref capture used so the impl lands on the SAME framer
        # scroll-scrub frame. The scrub is window.scrollY-driven; recomputing the
        # impl scroll_y independently lands a different phase and inflates AE on
        # identical content (the realfood pyramid-zoom class: ref-vs-ref-calib
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
        # Kill Lenis/smooth-scroll first so the forced scroll is not reverted to
        # actualY=0 during settle (specific regression cross-impl scroll-mapping class).
        _run_agent_eval(session, _disable_smooth_scroll_js())
        _run_agent_eval(session, _scroll_js(target_y, scroller_selector))
        _run_agent_eval(session, _fixed_overlay_toggle_js(target_y > 0))
        time.sleep(0.1)
        _run_agent_eval(session, pause_js)
        conf: dict[str, Any] | None = None
        if not skip_finish:
            _run_agent_eval(session, finish_js)
            conf = _unwrap_eval_json(_run_agent_eval_text(session, _settle_js()))
        time.sleep(max(0.2, wait_scroll_settle))
        # V-1: assert the session viewport immediately before the shot.
        expect_w_raw = (os.environ.get("SECTION_CAPTURE_VIEW_W") or "").strip()
        if expect_w_raw.isdigit():
            _ensure_viewport(session, int(expect_w_raw))
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
        _run_screenshot(session, output_path)
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
    if forced_scroll_y is None and pinned and metrics is not None:
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

    confidence_map: dict[str, dict[str, Any]] = {}

    for match in matches:
        name = safe_section_name(match.get("name"))
        ref = match.get("ref")
        impl = match.get("impl")

        if isinstance(ref, dict) and not reuse_frozen_ref:
            rect = ref.get("rect")
            if isinstance(rect, dict):
                conf = _capture_one(
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
                    identity=ref,
                )
                if conf is not None:
                    confidence_map.setdefault(name, {})["ref"] = conf
                    _ay = conf.get("actualY")
                    if _is_number(_ay):
                        ref_positions[name] = float(_ay)

        if isinstance(impl, dict):
            rect = impl.get("rect")
            if isinstance(rect, dict):
                conf = _capture_one(
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
                    identity=impl,
                    forced_scroll_y=(forced_positions.get(name) if reuse_frozen_ref else None),
                )
                if conf is not None:
                    confidence_map.setdefault(name, {})["impl"] = conf
                    if not reuse_frozen_ref:
                        _ay = conf.get("actualY")
                        if _is_number(_ay):
                            impl_positions[name] = float(_ay)

        sys.stdout.write(f"  ✓ {name}\n")
        sys.stdout.flush()

    # Persist the ref scroll positions so a later frozen-mode impl capture lands
    # on the same scroll-scrub frame (batch-13 ITEM 1 capture determinism).
    if ref_positions:
        positions_path.write_text(
            json.dumps(ref_positions, indent=2) + "\n", encoding="utf-8"
        )
    if impl_positions:
        (section_dir / "impl-scroll-positions.json").write_text(
            json.dumps(impl_positions, indent=2) + "\n",
            encoding="utf-8",
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
            subprocess.run(
                ["agent-browser", "--session", calib_session, "set", "viewport", calib_vw, calib_vh],
                capture_output=True, text=True, check=False,
            )
        subprocess.run(
            ["agent-browser", "--session", calib_session, "open", ref_url],
            capture_output=True, text=True, check=False,
        )
        subprocess.run(
            ["agent-browser", "--session", calib_session, "wait", "2500"],
            capture_output=True, text=True, check=False,
        )
        for match in matches:
            name = safe_section_name(match.get("name"))
            ref = match.get("ref")
            if not isinstance(ref, dict):
                continue
            rect = ref.get("rect")
            if not isinstance(rect, dict):
                continue
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
            sys.stdout.write(f"  ◇ calib {name}\n")
            sys.stdout.flush()
        subprocess.run(
            ["agent-browser", "--session", calib_session, "close"],
            capture_output=True, text=True, check=False,
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
