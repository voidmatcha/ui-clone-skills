"""In-page JavaScript sources evaluated by section capture (scroll, settle,
overlay masking, runtime normalization, live section rect probing).

Moved verbatim out of ``ui_clone.section_capture``; that module re-exports
every name here.
"""

from __future__ import annotations

import json
import os

from ui_clone.section_capture_primitives import _fmt_num


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
