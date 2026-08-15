// visible-identity.js — shared "resolve the rendered-visible target" helper.
//
// ITEM 0 of the adversarial-hardening batch. Every probe gate that reads the
// live DOM (masked-region-static, state-reveal, alignment-parity, hover,
// junk-token) uses this to:
//   1. emit RICH per-element records (paint + geometry), not bare style/box;
//   2. resolve a selector's matches to the rendered-VISIBLE element(s),
//      failing loud when the visible cardinality != what the ref expects so a
//      decoy cannot absorb the comparison;
//   3. take the SETTLED value of a time-varying property, not a transient.
//
// The pure predicates below mirror ui_clone/gates/visible_identity.py byte for
// byte (same thresholds). DOM access is isolated to collect()/sample() so the
// predicates are unit-testable under node with plain records.
//
// Browser usage (agent-browser eval — one unescape pass, so NO backslash
// regexes anywhere in this file): a probe prepends this source then evaluates
//   (() => JSON.stringify(__visibleIdentity.collect(sel, props)))()
// Node usage: require() returns the same api object.
(function (factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof globalThis !== "undefined") globalThis.__visibleIdentity = api;
  return api;
})(function () {
  "use strict";

  // ── shared thresholds (mirror visible_identity.py) ──
  var MIN_AREA_PX2 = 4;
  var MIN_FONT_PX = 4;
  var DEFAULT_MARGIN_PX = 16;
  var ALPHA_FLOOR = 0.1;
  var MIN_CONTRAST = 1.06;
  // batch-9 ITEM 1: multi-point, paint-aware occlusion. A foreign topmost node
  // counts as occluding a sample point ONLY when it paints materially opaque
  // pixels (effective opacity*bg-alpha, a replaced element, or a covering
  // background image); a translucent scrim (rgba(...,0.04)) or fully transparent
  // overlay does not. The element is "blocked" only when a MATERIAL fraction of
  // the sampled grid points across its content rect are opaquely occluded — a
  // 95%-covered text with a clear centre no longer reads "self".
  var OPAQUE_ALPHA = 0.5;
  var MATERIAL_OCCLUSION = 0.5;
  var OCCLUSION_COLS = 5;
  var OCCLUSION_ROWS = 3;
  // batch-9 ITEM 5: contrast is skipped only when the effective bg-image OPAQUELY
  // covers at least this fraction of the text rect (sampled from the decoded
  // image). A mostly-transparent image with one opaque pixel falls below it.
  var BG_IMAGE_COVERAGE_FLOOR = 0.1;

  function num(value, dflt) {
    if (value === null || value === undefined || value === "") return dflt;
    var n = typeof value === "number" ? value : parseFloat(value);
    return isNaN(n) ? dflt : n;
  }

  function rectOf(rec) {
    return rec && rec.rect && typeof rec.rect === "object" ? rec.rect : {};
  }

  function areaOf(rec) {
    if (rec && rec.area !== null && rec.area !== undefined) return num(rec.area, 0);
    var r = rectOf(rec);
    return num(r.width, 0) * num(r.height, 0);
  }

  function opacityOf(rec) {
    return num(rec.opacity, 1);
  }

  function isLaidOut(rec, opts) {
    opts = opts || {};
    var minArea = opts.minArea === undefined ? MIN_AREA_PX2 : opts.minArea;
    if (String(rec.display || "").toLowerCase() === "none") return false;
    if (String(rec.visibility || "").toLowerCase() === "hidden") return false;
    if (opacityOf(rec) <= 0) return false;
    var r = rectOf(rec);
    if (num(r.width, 0) <= 0 || num(r.height, 0) <= 0) return false;
    return areaOf(rec) >= minArea;
  }

  function viewportOf(rec, viewport) {
    if (viewport) return viewport;
    return [num(rec.clientWidth, 0), num(rec.clientHeight, 0)];
  }

  function isOnScreen(rec, opts) {
    opts = opts || {};
    var margin = opts.margin === undefined ? DEFAULT_MARGIN_PX : opts.margin;
    var vp = viewportOf(rec, opts.viewport);
    var vpW = vp[0], vpH = vp[1];
    var r = rectOf(rec);
    var left = num(r.left, 0);
    var top = num(r.top, 0);
    var right = left + num(r.width, 0);
    var bottom = top + num(r.height, 0);
    if (right <= -margin || left >= vpW + margin) return false;
    // below_fold_ok: a below-fold element (reachable by scroll) stays on-screen;
    // the x-axis + above-viewport gates still reject an off-screen decoy.
    if (vpH > 0) {
      if (bottom <= -margin) return false;
      if (top >= vpH + margin && !opts.belowFoldOk) return false;
    }
    return true;
  }

  // Browser-computed RENDER truth — close the imperceptibility CLASS. Only
  // rejects when a truth field is explicitly present AND hiding, so legacy
  // records keep their geometry behaviour.
  function isRendered(rec) {
    if (rec.checkVisibility === false) return false;
    if (rec.clipFullyHidden === true) return false;
    if (rec.filterOpacityZero === true) return false;
    if (rec.ancestorClipped === true) return false;
    if (rec.contentVisibilityHidden === true) return false;
    if (rec.textIndentHidden === true) return false;
    var ht = rec.hitTest;
    // null/absent == unknown (off-viewport / pointer-events:none) — not hidden.
    if (typeof ht === "string" && ht !== "" && ht !== "self" && ht !== "descendant") return false;
    return true;
  }

  function parseRgb(value) {
    if (value && typeof value.length === "number" && value.length >= 3 && typeof value !== "string") {
      var a = parseFloat(value[0]), b = parseFloat(value[1]), c = parseFloat(value[2]);
      if (isNaN(a) || isNaN(b) || isNaN(c)) return null;
      return [a, b, c];
    }
    if (typeof value === "string") {
      var s = value.trim().toLowerCase();
      var open = s.indexOf("("), close = s.indexOf(")");
      if (open < 0 || close < 0) return null;
      var parts = s.substring(open + 1, close).split(",");
      if (parts.length < 3) return null;
      var r = parseFloat(parts[0]), g = parseFloat(parts[1]), bl = parseFloat(parts[2]);
      if (isNaN(r) || isNaN(g) || isNaN(bl)) return null;
      return [r, g, bl];
    }
    return null;
  }

  function relLum(rgb) {
    function chan(c) { c = c / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2]);
  }

  function contrastRatio(fg, bg, alpha) {
    var a = Math.max(0, Math.min(1, alpha === undefined ? 1 : alpha));
    var blended = [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a)];
    var l1 = relLum(blended), l2 = relLum(bg);
    var hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  function paintsText(rec, opts) {
    opts = opts || {};
    var minFont = opts.minFont === undefined ? MIN_FONT_PX : opts.minFont;
    var alphaFloor = opts.alphaFloor === undefined ? ALPHA_FLOOR : opts.alphaFloor;
    var minContrast = opts.minContrast === undefined ? MIN_CONTRAST : opts.minContrast;
    if (!rec.hasText) return false;
    if (num(rec.colorAlpha, 1) < alphaFloor) return false;
    if (rec.fontSizePx !== undefined && rec.fontSizePx !== null && num(rec.fontSizePx, 0) <= minFont) return false;
    // Skip contrast ONLY with positive evidence the bg-image PAINTS opaque
    // pixels over the text box (a real hero photo — unknown but readable colour).
    // A 1x1/transparent/empty bg-image or a same-colour gradient is NOT evidence
    // (effectiveBgImagePaints false), so contrast still runs and invisible
    // white-on-white is caught. alpha/font floors above always apply.
    // batch-9 ITEM 5: skip contrast ONLY when the bg-image paints AND its sampled
    // opaque coverage under the text rect clears the floor — a "paints" flag with
    // a mostly-transparent image (or an undecoded image, coverage 0) no longer
    // auto-passes invisible text.
    var skipContrast = rec.effectiveBgImagePaints &&
      num(rec.bgImageOpaqueCoverage, 0) >= BG_IMAGE_COVERAGE_FLOOR;
    if (!skipContrast) {
      var fg = parseRgb(rec.color);
      var bg = parseRgb(rec.effectiveBgColor);
      if (fg && bg && contrastRatio(fg, bg, num(rec.colorAlpha, 1)) < minContrast) return false;
    }
    return true;
  }

  function paintsContent(rec, opts) {
    if (rec.replaced) return true;
    if (rec.pseudoHasContent) return true;
    if (num(rec.bgColorAlpha, 0) > 0) return true;
    if (rec.hasBgImage) return true;
    return paintsText(rec, opts);
  }

  function isVisible(rec, opts) {
    opts = opts || {};
    var requirePaint = opts.requirePaint === undefined ? true : opts.requirePaint;
    if (!isRendered(rec)) return false;
    if (!isLaidOut(rec, opts)) return false;
    if (!isOnScreen(rec, opts)) return false;
    if (requirePaint && !paintsContent(rec, opts)) return false;
    return true;
  }

  function resolveVisible(records, opts) {
    opts = opts || {};
    var expected = opts.expected === undefined ? 1 : opts.expected;
    var visible = [];
    for (var i = 0; i < records.length; i++) {
      if (isVisible(records[i], opts)) visible.push(records[i]);
    }
    if (visible.length === expected) {
      return {
        status: "ok",
        visible: visible,
        target: expected === 1 ? visible[0] : null,
        reason: visible.length + " visible match(es) == expected " + expected,
      };
    }
    if (visible.length > expected) {
      return {
        status: "ambiguous",
        visible: visible,
        target: null,
        reason: visible.length + " visible matches > expected " + expected +
          " (ambiguous decoy/duplicate)",
      };
    }
    return {
      status: "none",
      visible: visible,
      target: null,
      reason: visible.length + " visible matches < expected " + expected +
        " (rendered-visible target not found)",
    };
  }

  // ── settle ──
  function settledValue(samples) {
    if (!samples || !samples.length) throw new Error("settledValue requires a sample");
    return samples[samples.length - 1];
  }

  // Mirror of python settled_state: the SETTLED (last) value PLUS whether the
  // series actually reached quiescence (the trailing `frames` agree, by deep
  // value via JSON, like quiescent()). batch-9 minor: a consumer that hit the
  // maxMs cap WITHOUT quiescence reads settled:false (inconclusive) rather than
  // trusting the last value — so a series resolved at the cap is reportable as
  // non-quiescent, not silently accepted.
  function settledState(samples, frames) {
    if (!samples || !samples.length) throw new Error("settledState requires a sample");
    var f = Math.max(2, frames || 3);
    var n = Math.min(f, samples.length);
    var tail = samples.slice(samples.length - n);
    var ref = JSON.stringify(tail[0]);
    var settled = true;
    for (var i = 1; i < tail.length; i++) {
      if (JSON.stringify(tail[i]) !== ref) { settled = false; break; }
    }
    return { value: samples[samples.length - 1], settled: settled };
  }

  function isSettled(samples, opts) {
    opts = opts || {};
    var window = opts.window === undefined ? 2 : opts.window;
    if (samples.length < window) {
      for (var i = 1; i < samples.length; i++) {
        if (samples[i] !== samples[0]) return false;
      }
      return true;
    }
    var tail = samples.slice(samples.length - window);
    for (var j = 1; j < tail.length; j++) {
      if (tail[j] !== tail[0]) return false;
    }
    return true;
  }

  // Deep quiescence over snapshots (objects): the last `frames` snapshots are
  // JSON-equal. Pure (node-testable).
  function quiescent(samples, frames) {
    var f = Math.max(2, frames || 3);
    if (!samples || samples.length < f) return false;
    var tail = samples.slice(samples.length - f);
    var ref = JSON.stringify(tail[0]);
    for (var i = 1; i < tail.length; i++) {
      if (JSON.stringify(tail[i]) !== ref) return false;
    }
    return true;
  }

  // True settle: poll collectFn once per frame, accumulating EVERY snapshot,
  // until the last N snapshots are quiescent AND no DOM mutation landed this
  // frame AND a RANDOMIZED post-floor deadline has passed. Records all samples
  // (even equal ones) so a late flip / oscillation is never structurally
  // invisible. The deadline is jittered into the upper [floor, maxMs] band so
  // the resolve-without-quiescence point is no longer a single knowable number
  // an adversary can time a flip just past (the old fixed floor+4000 cap let a
  // defect deferred past 12s pass). maxMs only bounds runaway (never removed, or
  // a forever-oscillator would hang). clock/raf/rand are injectable for
  // deterministic tests; default to the live browser globals. Browser-only
  // (requestAnimationFrame / MutationObserver / performance.now); node --check
  // validates syntax.
  function sampleUntilQuiescent(collectFn, opts) {
    opts = opts || {};
    var frames = Math.max(2, opts.frames || 3);
    var floorMs = Math.max(8000, opts.floorMs || 8000);
    var maxMs = Math.max(floorMs + 8000, opts.maxMs || floorMs + 8000);
    var rand = opts.rand || Math.random;
    var clock = opts.now || function () {
      return (typeof performance !== "undefined" && performance.now)
        ? performance.now() : Date.now();
    };
    var start = clock();
    var elapsed = function () { return clock() - start; };
    var raf = opts.raf || ((typeof requestAnimationFrame === "function")
      ? function (cb) { requestAnimationFrame(function () { setTimeout(cb, 60); }); }
      : function (cb) { setTimeout(cb, 60); });
    // At least one randomized late re-probe past this jittered deadline before
    // accepting quiescence, so a defect that flips just after the floor (or
    // after the old fixed cap) still lands in the series.
    var probeDeadline = floorMs + (0.4 + rand() * 0.6) * (maxMs - floorMs);
    var pending = 0;
    var mo = null;
    if (typeof MutationObserver === "function" && typeof document !== "undefined") {
      mo = new MutationObserver(function (recs) { pending += recs.length; });
      try {
        mo.observe(document.documentElement, {
          attributes: true, childList: true, subtree: true, characterData: true,
        });
      } catch (e) { mo = null; }
    }
    var series = [];
    return new Promise(function (resolve) {
      function step() {
        series.push(collectFn());
        var noPending = pending === 0;
        pending = 0; // a frame with mutations resets quiescence
        var t = elapsed();
        var pastFloor = t >= floorMs && t >= probeDeadline;
        if ((pastFloor && noPending && quiescent(series, frames)) || t >= maxMs) {
          if (mo) mo.disconnect();
          resolve(series);
          return;
        }
        raf(step);
      }
      step();
    });
  }

  // ── computed-color alpha, parsed without regex (escaping-proof) ──
  function parseAlpha(colorStr) {
    if (!colorStr) return 0;
    var s = String(colorStr).trim().toLowerCase();
    if (s === "transparent") return 0;
    var open = s.indexOf("(");
    var close = s.indexOf(")");
    if (open < 0 || close < 0) return 1; // named/hex opaque
    var parts = s.substring(open + 1, close).split(",");
    if (parts.length >= 4) {
      var a = parseFloat(parts[3]);
      return isNaN(a) ? 1 : a;
    }
    return 1; // rgb()/hsl() with no alpha channel is opaque
  }

  var REPLACED_TAGS = {
    img: 1, svg: 1, canvas: 1, video: 1, picture: 1,
    iframe: 1, object: 1, embed: 1,
  };

  // ── DOM collector (browser only) ──
  // Builds a rich record per match for `selector`. `extraProps` (optional
  // array of computed-style property names) are attached under `.styles` so
  // gates can keep reading the specific properties they compared before.
  function collect(selector, extraProps) {
    var out = [];
    var els = [];
    try { els = Array.prototype.slice.call(document.querySelectorAll(selector)); }
    catch (e) { els = []; }
    var docEl = document.documentElement;
    var vpW = docEl.clientWidth;
    var vpH = docEl.clientHeight;
    for (var i = 0; i < els.length; i++) {
      out.push(describe(els[i], selector, i, extraProps, vpW, vpH));
    }
    return out;
  }

  // ── browser-only RENDER-truth helpers (close the imperceptibility class) ──
  // No backslash regexes (agent-browser eval applies one unescape pass); plain
  // string ops only.
  function nospace(s) { return String(s || "").split(" ").join(""); }

  function elemCheckVisibility(el) {
    if (typeof el.checkVisibility !== "function") return undefined; // unsupported -> unknown
    try {
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true });
    } catch (e) {
      try { return el.checkVisibility(); } catch (e2) { return undefined; }
    }
  }

  function clipFullyHidden(cs) {
    var cp = nospace(cs.getPropertyValue("clip-path"));
    if (cp.indexOf("inset(100%") >= 0 || cp === "circle(0)" || cp.indexOf("circle(0px") >= 0 || cp.indexOf("circle(0%") >= 0) return true;
    var clip = nospace(cs.getPropertyValue("clip"));
    if (clip === "rect(0px,0px,0px,0px)" || clip === "rect(0,0,0,0)") return true;
    return false;
  }

  function filterOpacityZero(cs) {
    var f = nospace(cs.getPropertyValue("filter"));
    return f.indexOf("opacity(0)") >= 0 || f.indexOf("opacity(0%)") >= 0 || f.indexOf("opacity(0.0)") >= 0;
  }

  // content-visibility:hidden ALWAYS skips its subtree's box + text rendering
  // (unlike :auto, which checkVisibility already reports off-screen). The
  // element's own border-box may still lay out via contain-intrinsic-size, so
  // geometry passes while the user sees nothing — read the computed value
  // directly.
  function contentVisibilityHidden(cs) {
    return nospace(cs.getPropertyValue("content-visibility")) === "hidden";
  }

  // A large off-box text-indent (typically with overflow:hidden) shoves the
  // glyphs outside the element's own clip — the box may paint but no glyph
  // lands in the painted rect. Flag only when the indent magnitude clears the
  // box width by a wide margin AND the box clips the spill (an honest deep
  // first-line indent never clears width+1000, and an un-clipped negative
  // indent still paints glyphs spilling on-screen, so neither is flagged).
  function textIndentHidden(cs, r) {
    var ti = parseFloat(cs.getPropertyValue("text-indent"));
    if (isNaN(ti) || ti === 0) return false;
    var w = r && r.width ? r.width : 0;
    var ov = nospace(cs.getPropertyValue("overflow")) +
      nospace(cs.getPropertyValue("overflow-x"));
    var clipped = ov.indexOf("hidden") >= 0 || ov.indexOf("clip") >= 0;
    return clipped && Math.abs(ti) >= w + 1000;
  }

  function clipsOverflowAxis(value) {
    value = nospace(value);
    return value === "hidden" || value === "clip" ||
      value === "auto" || value === "scroll" ||
      value === "overlay";
  }

  function ancestorClipped(el, r) {
    var p = el.parentElement;
    while (p && p !== document.documentElement) {
      var ps = getComputedStyle(p);
      var shorthand = ps.getPropertyValue("overflow");
      var overflowX = ps.getPropertyValue("overflow-x") || shorthand;
      var overflowY = ps.getPropertyValue("overflow-y") || shorthand;
      var clipsX = clipsOverflowAxis(overflowX);
      var clipsY = clipsOverflowAxis(overflowY);
      if (clipsX || clipsY) {
        var pr = p.getBoundingClientRect();
        if (clipsX && (r.right <= pr.left || r.left >= pr.right)) return true;
        if (clipsY && (r.bottom <= pr.top || r.top >= pr.bottom)) return true;
      }
      p = p.parentElement;
    }
    return false;
  }

  // batch-10 ITEM 2: does a foreign node's url() background-image opaquely cover
  // the glyph region enough to occlude? A sampled coverage at/above OPAQUE_ALPHA
  // occludes; below it the text shows through a transparent region (the FP fix).
  // null/undefined coverage (cross-origin taint, undecoded, or no canvas — the
  // sampler could not measure it) stays CONSERVATIVELY opaque, so a genuine
  // opaque cover is never let through and occlusion detection is not loosened.
  function bgImageOccludes(coverage) {
    if (coverage === null || coverage === undefined) return true;
    return coverage >= OPAQUE_ALPHA;
  }

  // Memoize the (expensive) per-node coverage decode within one hitTestAt grid.
  function coverageFor(node, region, memo) {
    if (memo) {
      for (var i = 0; i < memo.length; i++) {
        if (memo[i].node === node) return memo[i].cov;
      }
    }
    var cov = bgImageOpaqueCoverage(node, region);
    if (memo) memo.push({ node: node, cov: cov });
    return cov;
  }

  // Whether a foreign node painted over the text actually obscures it. A node
  // occludes only when it paints materially OPAQUE pixels: effective
  // opacity*background-color alpha at/above OPAQUE_ALPHA, a replaced element
  // (canvas/img/iframe/video — it paints its own content), or a background image
  // that OPAQUELY COVERS the glyph region (batch-10 ITEM 2 — no longer a blanket
  // "has url() image"). A transparent overlay (rgba(...,0)), a translucent
  // sticky-nav scrim (rgba(...,0.04)), or a PNG transparent over the text lets it
  // show through and does NOT count — closing the topology-only false-positive.
  function paintsOpaque(node, region, memo) {
    if (!node) return false;
    var cs;
    try { cs = getComputedStyle(node); } catch (e) { return false; }
    var op = num(cs.getPropertyValue("opacity"), 1);
    if (op < OPAQUE_ALPHA) return false; // a see-through layer cannot occlude
    var tag = node.tagName ? String(node.tagName).toLowerCase() : "";
    if (REPLACED_TAGS[tag] === 1) return true;
    var bgAlpha = parseAlpha(cs.getPropertyValue("background-color"));
    if (op * bgAlpha >= OPAQUE_ALPHA) return true;
    var img = cs.getPropertyValue("background-image");
    if (img && img.indexOf("url(") >= 0) {
      return bgImageOccludes(coverageFor(node, region, memo));
    }
    return false;
  }

  // Classify ONE sampled point: scan the elementsFromPoint stack from topmost
  // down. Reaching el (or its own descendant content, or a transparent ancestor
  // wrapper that el shows through) before any opaque foreign node => the point
  // is not occluded. Hitting an opaque foreign node first => occluded
  // ("blocked"). Translucent foreign nodes are skipped (el may show through
  // below them). "unknown" when el is absent at this point and nothing opaque
  // covers it (not counted toward the occlusion fraction).
  function classifyPoint(el, stack, region, memo) {
    for (var k = 0; k < stack.length; k++) {
      var node = stack[k];
      if (node === el) return "self";
      if (el.contains && el.contains(node)) return "descendant"; // el's own content
      if (node && node.contains && node.contains(el)) return "self"; // ancestor wrapper
      if (paintsOpaque(node, region, memo)) return "blocked";
    }
    return "unknown";
  }

  // Sample a grid of points across the element's content rect, clipped to the
  // viewport. Off-viewport points are dropped (never measured).
  function occlusionSamplePoints(r, vpW, vpH) {
    var pts = [];
    for (var j = 0; j < OCCLUSION_ROWS; j++) {
      for (var i = 0; i < OCCLUSION_COLS; i++) {
        var x = r.left + r.width * (i + 0.5) / OCCLUSION_COLS;
        var y = r.top + r.height * (j + 0.5) / OCCLUSION_ROWS;
        if (x < 0 || y < 0 || x > vpW || y > vpH) continue;
        pts.push({ x: x, y: y });
      }
    }
    return pts;
  }

  // Pure verdict from the occlusion tally (mirrored in visible_identity.py):
  // null when no point could be measured; "blocked" when a MATERIAL fraction of
  // measured points are opaquely occluded; "self" otherwise.
  function occludedVerdict(occluded, measured) {
    if (!measured) return null;
    return (occluded / measured) >= MATERIAL_OCCLUSION ? "blocked" : "self";
  }

  // batch-10 ITEM 1: descendant text nodes carrying real (non-whitespace) text.
  // The painted GLYPH extent is measured from these, not the element box.
  function textNodesOf(el) {
    var out = [];
    function walk(n) {
      var kids = n && n.childNodes;
      if (!kids) return;
      for (var i = 0; i < kids.length; i++) {
        var c = kids[i];
        if (!c) continue;
        if (c.nodeType === 3) {
          if (String(c.nodeValue || "").trim()) out.push(c);
        } else if (c.nodeType === 1) {
          walk(c);
        }
      }
    }
    walk(el);
    return out;
  }

  // batch-10 ITEM 1: the painted glyph rectangles of an element — what
  // Range.getClientRects() reports for its text node(s). A block-level heading's
  // bounding box includes empty space beyond the glyphs (a short centered title
  // in a wide column), so sampling occlusion over that box area false-reads
  // "blocked" when opaque decorations flank the empty sides. Sampling the glyph
  // rectangles (union of line-box runs) measures occlusion of the ACTUAL painted
  // text instead. Returns null when no text node resolves (a non-text/replaced
  // element, or no createRange) so the caller falls back to the bounding rect.
  // Browser-only (Range/getClientRects); node --check validates syntax.
  function glyphRectsOf(el) {
    if (typeof document === "undefined" || !document.createRange) return null;
    var tnodes = textNodesOf(el);
    if (!tnodes.length) return null;
    var rects = [];
    for (var i = 0; i < tnodes.length; i++) {
      var range;
      try { range = document.createRange(); range.selectNodeContents(tnodes[i]); }
      catch (e) { continue; }
      var rs;
      try { rs = range.getClientRects(); } catch (e2) { rs = null; }
      if (!rs) continue;
      for (var j = 0; j < rs.length; j++) {
        var rc = rs[j];
        if (rc && num(rc.width, 0) > 0 && num(rc.height, 0) > 0) {
          rects.push({ left: rc.left, top: rc.top, width: rc.width, height: rc.height });
        }
      }
    }
    return rects.length ? rects : null;
  }

  // Pure: the bounding rect spanning a list of rects (null for an empty list).
  function unionRect(rects) {
    if (!rects || !rects.length) return null;
    var l = rects[0].left, t = rects[0].top;
    var r = l + rects[0].width, b = t + rects[0].height;
    for (var i = 1; i < rects.length; i++) {
      l = Math.min(l, rects[i].left);
      t = Math.min(t, rects[i].top);
      r = Math.max(r, rects[i].left + rects[i].width);
      b = Math.max(b, rects[i].top + rects[i].height);
    }
    return { left: l, top: t, width: r - l, height: b - t };
  }

  // Pure: the occlusion grid distributed across EVERY rect of a (possibly
  // multi-line) glyph run, clipped to the viewport.
  function samplePointsForRects(rects, vpW, vpH) {
    var pts = [];
    for (var k = 0; k < rects.length; k++) {
      var sub = occlusionSamplePoints(rects[k], vpW, vpH);
      for (var i = 0; i < sub.length; i++) pts.push(sub[i]);
    }
    return pts;
  }

  // Occlusion backstop: MULTI-POINT + PAINT-AWARE. Sample a grid across el's
  // content rect; a point is occluded only when an OPAQUE foreign node is
  // topmost there. el is "blocked" when a material fraction of measured points
  // are occluded (partial/near-total covers with a clear centre now fail),
  // "self" when readable through transparent/translucent overlays, null when
  // unmeasurable (pointer-events:none on el, or the whole rect off-viewport).
  function hitTestAt(el, r, vpW, vpH) {
    try {
      if (getComputedStyle(el).getPropertyValue("pointer-events") === "none") return null;
    } catch (e) { /* no style engine — fall through to sampling */ }
    // batch-10 ITEM 1: sample the painted glyph extent, not the bounding box, so
    // a short heading in a wide block flanked by opaque decorations is read
    // through its glyphs. A non-text/replaced element (no glyph rects) falls
    // back to its content rect, preserving the prior behaviour.
    var rects = glyphRectsOf(el) || [r];
    // batch-10 ITEM 2: the glyph-extent union is the region a bg-image occluder
    // must opaquely cover; memo caches each node's coverage across the grid.
    var region = unionRect(rects);
    var memo = [];
    var pts = samplePointsForRects(rects, vpW, vpH);
    if (!pts.length) return null;
    var measured = 0, occluded = 0;
    for (var i = 0; i < pts.length; i++) {
      var stack;
      try { stack = document.elementsFromPoint(pts[i].x, pts[i].y); } catch (e) { stack = null; }
      if (!stack || !stack.length) continue;
      var rel = classifyPoint(el, stack, region, memo);
      if (rel === "unknown") continue;
      measured++;
      if (rel === "blocked") occluded++;
    }
    if (measured === 0) return null;
    return occludedVerdict(occluded, measured);
  }

  // tools-batch-11 ITEM 2: a position:fixed/absolute/sticky element is lifted
  // out of normal flow, so its DOM-ancestor chain (which leads to <body>) does
  // NOT describe the pixels painted behind it. Detect that context by walking up
  // from el: an opaque background-color on el or an in-flow ancestor IS the
  // backdrop (the ancestor walk already returns it, so stay with it); reaching
  // an out-of-flow element first means the DOM chain above no longer reflects the
  // visual backdrop, so the visual stack must be read instead.
  function isOverlayContext(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var cs;
      try {
        cs = getComputedStyle(node);
      } catch (e) {
        return false;
      }
      if (parseAlpha(cs.getPropertyValue("background-color")) >= 1) return false;
      var pos = cs.getPropertyValue("position");
      if (pos === "fixed" || pos === "absolute" || pos === "sticky") return true;
      node = node.parentElement;
    }
    return false;
  }

  // The opaque background colour painted BEHIND el AT ONE sampled point: scan the
  // visual stack (elementsFromPoint result) top-down, skipping el, el's own
  // descendants (painted above el), and any translucent foreign/ancestor node el
  // shows through (paintsOpaque false), returning the first opaque painting node's
  // solid background colour. Returns null when nothing opaque resolves a solid
  // colour at this point (an opaque image/replaced node first, or no opaque node).
  function firstOpaqueBgAt(el, stack, region, memo) {
    for (var k = 0; k < stack.length; k++) {
      var n = stack[k];
      if (!n || n.nodeType !== 1) continue;
      if (n === el) continue; // el itself
      if (el.contains && el.contains(n)) continue; // el's own content, painted above
      if (!paintsOpaque(n, region, memo)) continue; // translucent layer el shows through
      var bg = getComputedStyle(n).getPropertyValue("background-color");
      if (parseAlpha(bg) > 0) {
        var rgb = parseRgb(bg);
        if (rgb) return rgb;
      }
      return null; // opaque via image/replaced, no solid colour — caller falls back
    }
    return null;
  }

  // tools-batch-12 ITEM 1: the background colour painted BEHIND el, read
  // MULTI-POINT across the painted glyph extent — not from a single centre
  // sample. A lone centre point misjudges text that spans a region: a stray
  // centre hit on a light node false-FAILS a white-on-dark inverted nav label,
  // and a centre hit on a dark node false-PASSES invisible light-on-light text.
  // Mirroring hitTestAt, sample the glyph rects (falling back to the bounding
  // rect for a non-text/replaced element), resolve each sample's first opaque
  // solid backdrop, and return the MODAL colour — the backdrop behind the bulk
  // of the glyphs, robust to a single outlier sample in either direction. The
  // representative (not adversarial-worst) choice keeps the white-on-dark PASS
  // while a uniformly-light backdrop still yields a light modal (contrast FAIL
  // preserved). Returns null in a non-browser context, when the geometry/stack
  // is unavailable, or when no sample resolves a solid colour — the caller then
  // falls back to the DOM-ancestor walk. Browser-only-guarded so node --check and
  // the pure node harness still run.
  function stackedBgColor(el) {
    if (typeof document === "undefined" || !document.elementsFromPoint) return null;
    if (!el || !el.getBoundingClientRect) return null;
    var r;
    try {
      r = el.getBoundingClientRect();
    } catch (e) {
      return null;
    }
    if (!r || !(r.width > 0) || !(r.height > 0)) return null;
    var rects = glyphRectsOf(el) || [r];
    var region = unionRect(rects) || r;
    var vpW = (typeof window !== "undefined" && window.innerWidth > 0)
      ? window.innerWidth : Number.MAX_SAFE_INTEGER;
    var vpH = (typeof window !== "undefined" && window.innerHeight > 0)
      ? window.innerHeight : Number.MAX_SAFE_INTEGER;
    var pts = samplePointsForRects(rects, vpW, vpH);
    if (!pts.length) pts = [{ x: r.left + r.width / 2, y: r.top + r.height / 2 }];
    var memo = [];
    var tally = []; // [{ key, rgb, count }] — modal opaque backdrop colour
    for (var p = 0; p < pts.length; p++) {
      var stack;
      try {
        stack = document.elementsFromPoint(pts[p].x, pts[p].y);
      } catch (e) {
        stack = null;
      }
      if (!stack || !stack.length) continue;
      var rgb = firstOpaqueBgAt(el, stack, region, memo);
      if (!rgb) continue;
      var key = rgb[0] + "," + rgb[1] + "," + rgb[2];
      var hit = null;
      for (var t = 0; t < tally.length; t++) {
        if (tally[t].key === key) { hit = tally[t]; break; }
      }
      if (hit) hit.count++;
      else tally.push({ key: key, rgb: rgb, count: 1 });
    }
    if (!tally.length) return null;
    var best = tally[0];
    for (var b = 1; b < tally.length; b++) {
      if (tally[b].count > best.count) best = tally[b];
    }
    return best.rgb;
  }

  function effectiveBgColor(el) {
    if (isOverlayContext(el)) {
      var stacked = stackedBgColor(el);
      if (stacked) return stacked;
    }
    var node = el;
    while (node && node.nodeType === 1) {
      var a = parseAlpha(getComputedStyle(node).getPropertyValue("background-color"));
      if (a > 0) {
        var rgb = parseRgb(getComputedStyle(node).getPropertyValue("background-color"));
        if (rgb) return rgb;
      }
      node = node.parentElement;
    }
    return [255, 255, 255]; // canvas default
  }

  // Whether the element or a walked ancestor paints a background IMAGE before an
  // opaque background-colour is reached — the effective background colour is then
  // unknown, so the contrast check must be skipped (hero-overlay false-positive
  // guard). Stops at the first opaque bg-color (it would occlude an image above).
  function effectiveBgIsImage(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var cs = getComputedStyle(node);
      var img = cs.getPropertyValue("background-image");
      if (img && img !== "none" && img !== "") return true;
      if (parseAlpha(cs.getPropertyValue("background-color")) >= 1) return false;
      node = node.parentElement;
    }
    return false;
  }

  // Pull the first url() target from a computed background-image using plain
  // string ops (no backslash regex — one-unescape pass).
  function bgImageUrl(img) {
    var i = img.indexOf("url(");
    if (i < 0) return "";
    var start = i + 4, end = img.indexOf(")", start);
    if (end < 0) return "";
    var u = img.substring(start, end).trim();
    if (u.length >= 2 && (u.charAt(0) === '"' || u.charAt(0) === "'")) {
      u = u.substring(1, u.length - 1);
    }
    return u;
  }

  // Average every rgb()/rgba() colour in a computed gradient string. getComputed
  // Style serialises stops as rgb(...)/rgba(...), so a same-colour gradient folds
  // to its one colour and a varied one to its mean — used as the contrast
  // backdrop so white-on-white-gradient is caught while white-on-dark passes.
  function avgGradientColor(s) {
    var sum = [0, 0, 0], n = 0, from = 0;
    while (true) {
      var i = s.indexOf("rgb", from);
      if (i < 0) break;
      var close = s.indexOf(")", i);
      if (close < 0) break;
      var c = parseRgb(s.substring(i, close + 1));
      if (c) { sum[0] += c[0]; sum[1] += c[1]; sum[2] += c[2]; n++; }
      from = close + 1;
    }
    if (!n) return null;
    return [sum[0] / n, sum[1] / n, sum[2] / n];
  }

  // Evidence the nearest background layer above the text PAINTS opaque covering
  // pixels (a real hero photo), so the contrast check can be skipped. A url()
  // image counts only when it covers the box (background-size cover/contain) and
  // — when the intrinsic size is decodable — is larger than a 1x1 placeholder.
  // A gradient is NOT skip-evidence (its colour is known): its average stop
  // colour is returned so contrast runs against the real backdrop instead.
  // Returns { paints: bool, gradColor: [r,g,b]|null }.
  function bgPaintEvidence(el) {
    var node = el;
    while (node && node.nodeType === 1) {
      var cs = getComputedStyle(node);
      var img = cs.getPropertyValue("background-image");
      if (img && img !== "none" && img !== "") {
        if (img.indexOf("url(") < 0) {
          return { paints: false, gradColor: avgGradientColor(img) };
        }
        var url = bgImageUrl(img);
        var area = 0, complete = false;
        try {
          if (url && typeof Image === "function") {
            var probe = new Image();
            probe.src = url;
            complete = !!probe.complete;
            if (complete) area = probe.naturalWidth * probe.naturalHeight;
          }
        } catch (e) { complete = false; }
        var size = nospace(cs.getPropertyValue("background-size"));
        var covers = size.indexOf("cover") >= 0 || size.indexOf("contain") >= 0;
        // Evidence = a decoded image larger than a 1x1 placeholder (a real
        // photo paints opaque pixels), OR a cover/contain layer when the
        // intrinsic size cannot be decoded yet (hero photos cover the box). A
        // 1x1/transparent placeholder (area<=1) never qualifies once decoded.
        return { paints: !!url && (area > 1 || (covers && !complete)), gradColor: null };
      }
      if (parseAlpha(cs.getPropertyValue("background-color")) >= 1) {
        return { paints: false, gradColor: null };
      }
      node = node.parentElement;
    }
    return { paints: false, gradColor: null };
  }

  // batch-10 ITEM 3: pure CSS background-size resolver. Returns the painted
  // [width, height] of the image inside the box for cover/contain/auto/explicit
  // lengths + percentages. (No backslash regex — split on single spaces, the
  // form getComputedStyle serialises.)
  function sizeLen(tok, boxDim, natDim) {
    if (tok === "auto" || tok === "") return null;
    if (tok.charAt(tok.length - 1) === "%") return boxDim * (parseFloat(tok) / 100);
    var v = parseFloat(tok);
    return isNaN(v) ? null : v;
  }

  function bgRenderDims(box, natW, natH, sizeStr) {
    var s = String(sizeStr || "auto").trim().toLowerCase();
    if (s.indexOf("cover") >= 0) {
      var sc = Math.max(box.width / natW, box.height / natH);
      return [natW * sc, natH * sc];
    }
    if (s.indexOf("contain") >= 0) {
      var sc2 = Math.min(box.width / natW, box.height / natH);
      return [natW * sc2, natH * sc2];
    }
    var toks = s.split(" ").filter(function (t) { return t.length; });
    var w = sizeLen(toks[0] || "auto", box.width, natW);
    var h = sizeLen(toks[1] || "auto", box.height, natH);
    if (w === null && h === null) { w = natW; h = natH; }
    else if (w === null) { w = natW * (h / natH); }
    else if (h === null) { h = natH * (w / natW); }
    return [w, h];
  }

  // batch-10 ITEM 3: pure CSS background-position resolver for ONE axis. A
  // percentage aligns that % point of the image with that % point of the box
  // ((box - render) * pct); a length offsets the image edge from the box edge.
  function posOffset(tok, boxDim, renderDim) {
    tok = String(tok === undefined || tok === "" ? "0%" : tok).trim().toLowerCase();
    if (tok === "left" || tok === "top") return 0;
    if (tok === "right" || tok === "bottom") return boxDim - renderDim;
    if (tok === "center") return (boxDim - renderDim) * 0.5;
    if (tok.charAt(tok.length - 1) === "%") return (boxDim - renderDim) * (parseFloat(tok) / 100);
    var v = parseFloat(tok);
    return isNaN(v) ? 0 : v;
  }

  // batch-10 ITEM 3: pure — the rendered image rect (viewport coords) inside the
  // box, from background-size + background-position. The contrast-skip / occlusion
  // samplers map the text region onto THIS rect, not the whole asset.
  function bgImageRenderRect(box, natW, natH, sizeStr, posStr) {
    var dims = bgRenderDims(box, natW, natH, sizeStr);
    var rw = dims[0], rh = dims[1];
    var p = String(posStr || "0% 0%").trim().toLowerCase();
    var toks = p.split(" ").filter(function (t) { return t.length; });
    var xt, yt;
    if (toks.length <= 1) {
      var only = toks[0] || "0%";
      if (only === "top" || only === "bottom") { xt = "center"; yt = only; }
      else { xt = only; yt = "center"; }
    } else {
      xt = toks[0]; yt = toks[1];
    }
    return {
      left: box.left + posOffset(xt, box.width, rw),
      top: box.top + posOffset(yt, box.height, rh),
      width: rw,
      height: rh,
    };
  }

  // batch-10 ITEM 3: parse background-repeat to [repeatX, repeatY].
  function parseRepeat(repeatStr) {
    var s = String(repeatStr || "repeat").trim().toLowerCase();
    if (s === "repeat-x") return [true, false];
    if (s === "repeat-y") return [false, true];
    if (s === "no-repeat") return [false, false];
    if (s === "repeat" || s === "space" || s === "round") return [true, true];
    var toks = s.split(" ").filter(function (t) { return t.length; });
    var x = toks[0] || "repeat", y = toks[1] || toks[0] || "repeat";
    return [x !== "no-repeat", y !== "no-repeat"];
  }

  // batch-10 ITEM 3: draw the bg-image into a canvas that MODELS the box (so
  // canvas coords are box-relative), tiling when background-repeat repeats so a
  // repeating opaque texture is not under-counted. Browser-only (ctx.drawImage).
  function drawBgTiles(ctx, probe, box, rr, repeatStr, BW, BH) {
    var sX = BW / box.width, sY = BH / box.height;
    var tw = rr.width * sX, th = rr.height * sY;
    if (tw <= 0 || th <= 0) return;
    var rep = parseRepeat(repeatStr);
    var tx0 = (rr.left - box.left) * sX, ty0 = (rr.top - box.top) * sY;
    var xs = [tx0], ys = [ty0], x, y, guard;
    if (rep[0]) {
      x = tx0 - tw; guard = 0;
      while (x > -tw && guard++ < 256) { xs.unshift(x); x -= tw; }
      x = tx0 + tw; guard = 0;
      while (x < BW && guard++ < 256) { xs.push(x); x += tw; }
    }
    if (rep[1]) {
      y = ty0 - th; guard = 0;
      while (y > -th && guard++ < 256) { ys.unshift(y); y -= th; }
      y = ty0 + th; guard = 0;
      while (y < BH && guard++ < 256) { ys.push(y); y += th; }
    }
    for (var a = 0; a < xs.length; a++) {
      for (var b = 0; b < ys.length; b++) ctx.drawImage(probe, xs[a], ys[b], tw, th);
    }
  }

  // batch-9 ITEM 5 + batch-10 ITEM 3: sample the OPAQUE coverage of the effective
  // background image UNDER A SPECIFIC REGION (the text/glyph rect). The image is
  // drawn into a canvas that models the bg node's box at its real
  // background-size/position placement; only the pixels under the region (mapped
  // box-relative) are counted, scaled to the FULL region area so a region that
  // overhangs the box (or lands on a transparent part of the asset) reads low
  // coverage. `region` defaults to the whole box. Returns the covered fraction
  // [0,1], 0 when the region does not overlap the bg box, or null when it cannot
  // be measured (no url image, undecoded, cross-origin taint, no canvas) — the
  // predicate treats null/absent as 0 so contrast runs (never auto-pass on weak
  // evidence). Browser-only (Image / canvas / getImageData); node --check
  // validates syntax and the pure placement math (bgImageRenderRect) is tested.
  function bgImageOpaqueCoverage(el, region) {
    if (typeof document === "undefined" || !document.createElement) return null;
    var node = el, bgNode = null, cs0 = null, url = "";
    while (node && node.nodeType === 1) {
      var cs = getComputedStyle(node);
      var img = cs.getPropertyValue("background-image");
      if (img && img !== "none" && img !== "") {
        if (img.indexOf("url(") < 0) return null; // a gradient is not a photo
        url = bgImageUrl(img); bgNode = node; cs0 = cs;
        break;
      }
      if (parseAlpha(cs.getPropertyValue("background-color")) >= 1) return null;
      node = node.parentElement;
    }
    if (!url || !bgNode || !cs0) return null;
    try {
      if (typeof Image !== "function") return null;
      var probe = new Image();
      probe.src = url;
      if (!probe.complete || !probe.naturalWidth || !probe.naturalHeight) return null;
      var box = bgNode.getBoundingClientRect ? bgNode.getBoundingClientRect() : null;
      if (!box || box.width <= 0 || box.height <= 0) return null;
      var reg = (region && num(region.width, 0) > 0 && num(region.height, 0) > 0)
        ? region
        : { left: box.left, top: box.top, width: box.width, height: box.height };
      // overlap of the text region with the bg box — only that part can be covered
      var ox = Math.max(reg.left, box.left);
      var oy = Math.max(reg.top, box.top);
      var oR = Math.min(reg.left + reg.width, box.left + box.width);
      var oB = Math.min(reg.top + reg.height, box.top + box.height);
      if (oR <= ox || oB <= oy) return 0; // region not over this bg box
      var BW = 64, BH = 64;
      var canvas = document.createElement("canvas");
      canvas.width = BW;
      canvas.height = BH;
      var ctx = canvas.getContext && canvas.getContext("2d");
      if (!ctx) return null;
      var rr = bgImageRenderRect(
        box, probe.naturalWidth, probe.naturalHeight,
        cs0.getPropertyValue("background-size"),
        cs0.getPropertyValue("background-position")
      );
      drawBgTiles(ctx, probe, box, rr, cs0.getPropertyValue("background-repeat"), BW, BH);
      var sx = Math.floor((ox - box.left) / box.width * BW);
      var sy = Math.floor((oy - box.top) / box.height * BH);
      var sw = Math.max(1, Math.round((oR - ox) / box.width * BW));
      var sh = Math.max(1, Math.round((oB - oy) / box.height * BH));
      if (sx < 0) sx = 0;
      if (sy < 0) sy = 0;
      if (sx + sw > BW) sw = BW - sx;
      if (sy + sh > BH) sh = BH - sy;
      if (sw <= 0 || sh <= 0) return 0;
      var data = ctx.getImageData(sx, sy, sw, sh).data; // throws on cross-origin taint
      var sampled = data.length / 4, opaque = 0;
      for (var i = 3; i < data.length; i += 4) {
        if (data[i] / 255 >= OPAQUE_ALPHA) opaque++;
      }
      if (!sampled) return 0;
      var opaqueFracOverlap = opaque / sampled;
      var overlapArea = (oR - ox) * (oB - oy);
      var regionArea = reg.width * reg.height;
      if (regionArea <= 0) return 0;
      return opaqueFracOverlap * (overlapArea / regionArea);
    } catch (e) {
      return null;
    }
  }

  function pseudoHasContent(el) {
    function has(c) { return c && c !== "none" && c !== "normal" && c !== '""' && c !== "''"; }
    try {
      return has(getComputedStyle(el, "::before").getPropertyValue("content")) ||
        has(getComputedStyle(el, "::after").getPropertyValue("content"));
    } catch (e) { return false; }
  }

  function describe(el, selector, index, extraProps, vpW, vpH) {
    var cs = getComputedStyle(el);
    var r = el.getBoundingClientRect();
    var bgEv = bgPaintEvidence(el);
    // batch-10 ITEM 3: the bg-image opaque-coverage and occlusion samplers both
    // measure the PAINTED TEXT region, not the whole box — use the glyph extent
    // (falling back to the content rect for a non-text element).
    var textRegion = unionRect(glyphRectsOf(el)) ||
      { left: r.left, top: r.top, width: r.width, height: r.height };
    var tag = el.tagName.toLowerCase();
    var styles = {};
    if (extraProps && extraProps.length) {
      for (var p = 0; p < extraProps.length; p++) {
        styles[extraProps[p]] = cs.getPropertyValue(extraProps[p]);
      }
    }
    var text = (el.innerText || el.textContent || "").trim();
    return {
      selector: selector,
      index: index,
      tag: tag,
      className: (el.className && el.className.toString ? el.className.toString() : "").substring(0, 120),
      display: cs.getPropertyValue("display"),
      visibility: cs.getPropertyValue("visibility"),
      opacity: num(cs.getPropertyValue("opacity"), 1),
      rect: {
        top: Math.round(r.top),
        left: Math.round(r.left),
        width: Math.round(r.width),
        height: Math.round(r.height),
      },
      colorAlpha: parseAlpha(cs.getPropertyValue("color")),
      color: parseRgb(cs.getPropertyValue("color")),
      effectiveBgColor: bgEv.gradColor || effectiveBgColor(el),
      effectiveBgIsImage: effectiveBgIsImage(el),
      effectiveBgImagePaints: bgEv.paints,
      bgImageOpaqueCoverage: bgEv.paints ? bgImageOpaqueCoverage(el, textRegion) : null,
      fontSizePx: num(cs.getPropertyValue("font-size"), 0),
      bgColorAlpha: parseAlpha(cs.getPropertyValue("background-color")),
      hasBgImage: cs.getPropertyValue("background-image") !== "none" &&
        cs.getPropertyValue("background-image") !== "",
      hasText: text.length > 0,
      pseudoHasContent: pseudoHasContent(el),
      replaced: REPLACED_TAGS[tag] === 1,
      checkVisibility: elemCheckVisibility(el),
      clipFullyHidden: clipFullyHidden(cs),
      filterOpacityZero: filterOpacityZero(cs),
      ancestorClipped: ancestorClipped(el, r),
      contentVisibilityHidden: contentVisibilityHidden(cs),
      textIndentHidden: textIndentHidden(cs, r),
      hitTest: hitTestAt(el, r, vpW, vpH),
      clientWidth: vpW,
      clientHeight: vpH,
      styles: styles,
    };
  }

  // A declared reveal target is often an overflow-hidden geometry wrapper while
  // a nested span owns the actual glyphs and may override inherited colour.
  // Select by DOM text ownership only — never by whether a candidate currently
  // paints — so transparent/opacity:0/font-size:0 descendants still reach the
  // existing fail-closed paint predicates.
  function textPaintTarget(el) {
    var owners = [];
    var seen = [];

    function visit(node, depth) {
      var children = node && node.childNodes ? node.childNodes : [];
      for (var i = 0; i < children.length; i++) {
        var child = children[i];
        if (child && child.nodeType === 3) {
          var text = String(child.nodeValue || "").trim();
          var owner = child.parentElement || node;
          if (!text || !owner) continue;
          var found = seen.indexOf(owner);
          if (found < 0) {
            seen.push(owner);
            owners.push({ el: owner, chars: text.length, depth: depth });
          } else {
            owners[found].chars += text.length;
            if (depth > owners[found].depth) owners[found].depth = depth;
          }
        } else if (child && child.nodeType === 1) {
          visit(child, depth + 1);
        }
      }
    }

    visit(el, 0);
    if (!owners.length) return el;
    owners.sort(function (a, b) {
      if (b.chars !== a.chars) return b.chars - a.chars;
      return b.depth - a.depth;
    });
    return owners[0].el;
  }

  function describeTextPaint(el, selector, index, extraProps, vpW, vpH) {
    return describe(textPaintTarget(el), selector, index, extraProps, vpW, vpH);
  }

  return {
    MIN_AREA_PX2: MIN_AREA_PX2,
    MIN_FONT_PX: MIN_FONT_PX,
    DEFAULT_MARGIN_PX: DEFAULT_MARGIN_PX,
    ALPHA_FLOOR: ALPHA_FLOOR,
    MIN_CONTRAST: MIN_CONTRAST,
    BG_IMAGE_COVERAGE_FLOOR: BG_IMAGE_COVERAGE_FLOOR,
    isLaidOut: isLaidOut,
    isOnScreen: isOnScreen,
    isRendered: isRendered,
    paintsText: paintsText,
    paintsContent: paintsContent,
    isVisible: isVisible,
    resolveVisible: resolveVisible,
    settledValue: settledValue,
    settledState: settledState,
    isSettled: isSettled,
    quiescent: quiescent,
    sampleUntilQuiescent: sampleUntilQuiescent,
    parseAlpha: parseAlpha,
    parseRgb: parseRgb,
    contrastRatio: contrastRatio,
    OPAQUE_ALPHA: OPAQUE_ALPHA,
    MATERIAL_OCCLUSION: MATERIAL_OCCLUSION,
    hitTestAt: hitTestAt,
    occludedVerdict: occludedVerdict,
    paintsOpaque: paintsOpaque,
    effectiveBgColor: effectiveBgColor,
    bgImageOccludes: bgImageOccludes,
    glyphRectsOf: glyphRectsOf,
    unionRect: unionRect,
    samplePointsForRects: samplePointsForRects,
    bgImageRenderRect: bgImageRenderRect,
    bgRenderDims: bgRenderDims,
    bgImageOpaqueCoverage: bgImageOpaqueCoverage,
    ancestorClipped: ancestorClipped,
    collect: collect,
    describe: describe,
    textPaintTarget: textPaintTarget,
    describeTextPaint: describeTextPaint,
  };
});
