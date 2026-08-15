"""Isolation tests for the JS half of the visible-identity primitive.

skills/visual-debug/scripts/lib/visible-identity.js is the browser-eval
collector that emits rich per-element records (paint + geometry) and carries
the SAME pure predicates as the Python mirror (ui_clone/gates/visible_identity.py).
We syntax-check it with `node --check` and exercise its pure functions with a
node harness so the JS and Python halves cannot drift apart.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "visible-identity.js"


def test_lib_exists() -> None:
    assert LIB.is_file(), f"missing {LIB}"


def test_node_syntax_check() -> None:
    if shutil.which("node") is None:
        pytest.skip("node required to syntax-check the browser eval")
    result = subprocess.run(
        ["node", "--check", str(LIB)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


HARNESS = r"""
const vi = require(process.argv[2]);
let failed = 0;
function ok(cond, msg) { if (!cond) { console.error("FAIL: " + msg); failed++; } }

function rec(over) {
  return Object.assign({
    selector: ".t", index: 0, tag: "h2", className: "t",
    display: "block", visibility: "visible", opacity: 1,
    rect: { top: 100, left: 200, width: 400, height: 40 },
    colorAlpha: 1, fontSizePx: 18, bgColorAlpha: 0, hasBgImage: false,
    hasText: true, replaced: false, clientWidth: 1280, clientHeight: 800,
  }, over || {});
}
const VP = [1280, 800];

// thresholds mirror the python module
ok(vi.MIN_AREA_PX2 === 4, "MIN_AREA_PX2");
ok(vi.MIN_FONT_PX === 4, "MIN_FONT_PX");
ok(vi.DEFAULT_MARGIN_PX === 16, "DEFAULT_MARGIN_PX");

// is_laid_out
ok(vi.isLaidOut(rec()) === true, "baseline laid out");
ok(vi.isLaidOut(rec({ display: "none" })) === false, "display none");
ok(vi.isLaidOut(rec({ visibility: "hidden" })) === false, "visibility hidden");
ok(vi.isLaidOut(rec({ opacity: 0 })) === false, "opacity 0");
ok(vi.isLaidOut(rec({ opacity: "0" })) === false, "opacity string 0");
ok(vi.isLaidOut(rec({ rect: { top: 0, left: 0, width: 0, height: 40 } })) === false, "width 0");

// is_on_screen
ok(vi.isOnScreen(rec(), { viewport: VP }) === true, "onscreen");
ok(vi.isOnScreen(rec({ rect: { top: 100, left: -99999, width: 95, height: 24 } }), { viewport: VP }) === false, "offscreen left");
ok(vi.isOnScreen(rec({ rect: { top: 100, left: 1400, width: 200, height: 24 } }), { viewport: VP }) === false, "offscreen right");

// paints
ok(vi.paintsText(rec()) === true, "normal text paints");
ok(vi.paintsText(rec({ colorAlpha: 0 })) === false, "transparent text");
ok(vi.paintsText(rec({ fontSizePx: 0 })) === false, "font-size 0");
const spacer = rec({ tag: "div", rect: { top: 0, left: 0, width: 400, height: 4 }, hasText: false, colorAlpha: 0, bgColorAlpha: 0, hasBgImage: false, replaced: false });
ok(vi.paintsContent(spacer) === false, "transparent spacer not content");
ok(vi.paintsContent(rec({ hasText: false, bgColorAlpha: 1 })) === true, "bg color content");
ok(vi.paintsContent(rec({ hasText: false, replaced: true })) === true, "replaced content");

// resolve_visible cardinality
let res = vi.resolveVisible([rec()], { expected: 1, viewport: VP });
ok(res.status === "ok" && res.target !== null, "single ok");
res = vi.resolveVisible([rec({ display: "none", className: "decoy" }), rec({ index: 1, className: "real" })], { expected: 1, viewport: VP });
ok(res.status === "ok" && res.target.className === "real", "decoy+real picks real");
res = vi.resolveVisible([rec({ index: 0 }), rec({ index: 1 })], { expected: 1, viewport: VP });
ok(res.status === "ambiguous", "two visible ambiguous");
res = vi.resolveVisible([rec({ display: "none" })], { expected: 1, viewport: VP });
ok(res.status === "none", "no visible none");

// settle
ok(vi.settledValue(["center", "center", "left", "left"]) === "left", "settled final");
ok(vi.isSettled(["center", "left", "left"]) === true, "settled true");
ok(vi.isSettled(["center", "center", "left"]) === false, "settled false");
// batch-9 minor: settledState reports quiescence, not just the value — a series
// resolved at the maxMs cap with a non-quiescent tail reads settled:false.
ok(typeof vi.settledState === "function", "settledState exported");
ok(vi.settledState(["c", "c", "c"], 3).settled === true, "settledState quiescent true");
ok(vi.settledState(["c", "c", "l"], 3).settled === false, "settledState cap-hit late flip not settled");
ok(vi.settledState(["c", "l", "c"], 3).settled === false, "settledState oscillating not settled");
ok(vi.settledState(["c", "c", "l"], 3).value === "l", "settledState value is last sample");
ok(vi.settledState([{ ta: "c" }, { ta: "c" }, { ta: "c" }], 3).settled === true, "settledState deep-equal quiescent");
ok(vi.settledState([{ ta: "c" }, { ta: "c" }, { ta: "l" }], 3).settled === false, "settledState deep-equal late flip");

// parseAlpha (no-regex computed-color parsing)
ok(vi.parseAlpha("rgba(0, 0, 0, 0)") === 0, "rgba alpha 0");
ok(vi.parseAlpha("rgb(1, 2, 3)") === 1, "rgb alpha 1");
ok(vi.parseAlpha("transparent") === 0, "transparent alpha 0");

// ── batch-7 ITEM 1: pixel-truth predicates mirror the python mirror ──
ok(vi.ALPHA_FLOOR === 0.1, "ALPHA_FLOOR");
ok(vi.MIN_CONTRAST === 1.06, "MIN_CONTRAST");
// isRendered closes the imperceptibility class
ok(vi.isRendered(rec()) === true, "rendered baseline");
ok(vi.isRendered(rec({ checkVisibility: false })) === false, "checkVisibility false hidden");
ok(vi.isRendered(rec({ clipFullyHidden: true })) === false, "clip hidden");
ok(vi.isRendered(rec({ filterOpacityZero: true })) === false, "filter opacity hidden");
ok(vi.isRendered(rec({ ancestorClipped: true })) === false, "ancestor clip hidden");
ok(vi.isRendered(rec({ hitTest: "blocked" })) === false, "hit blocked hidden");
ok(vi.isRendered(rec({ hitTest: null })) === true, "hit null not hidden");
ok(vi.isRendered(rec({ hitTest: "descendant" })) === true, "hit descendant ok");
ok(typeof vi.ancestorClipped === "function", "ancestorClipped exported");

// state-reveal geometry wrappers may delegate glyph paint to a nested span.
// Selection follows direct text-node ownership, not paintability, so an
// invisible text owner is still selected and rejected by paintsText downstream.
function textNode(value, parent) {
  return { nodeType: 3, nodeValue: value, parentElement: parent };
}
function elem(children) {
  return { nodeType: 1, childNodes: children || [] };
}
var PAINT_LEAF = elem();
PAINT_LEAF.childNodes = [textNode("Resources", PAINT_LEAF)];
var PAINT_WRAP = elem([elem([PAINT_LEAF])]);
ok(typeof vi.textPaintTarget === "function", "textPaintTarget exported");
ok(typeof vi.describeTextPaint === "function", "describeTextPaint exported");
ok(vi.textPaintTarget(PAINT_WRAP) === PAINT_LEAF, "nested text owner selected");
PAINT_LEAF.colorAlpha = 0;
ok(vi.textPaintTarget(PAINT_WRAP) === PAINT_LEAF, "transparent text owner is not bypassed");
var EMPTY_TEXT_WRAP = elem();
ok(vi.textPaintTarget(EMPTY_TEXT_WRAP) === EMPTY_TEXT_WRAP, "no-text wrapper falls back to itself");
function ancestorClip(overflowX, overflowY, parentRect, targetRect) {
  const savedDocument = global.document;
  const savedGetComputedStyle = global.getComputedStyle;
  const html = {};
  const body = {
    parentElement: html,
    getBoundingClientRect: function () { return parentRect; },
  };
  const el = { parentElement: body };
  global.document = { documentElement: html, body: body };
  global.getComputedStyle = function () {
    return {
      getPropertyValue: function (name) {
        if (name === "overflow-x") return overflowX;
        if (name === "overflow-y") return overflowY;
        if (name === "overflow") return overflowX + " " + overflowY;
        return "";
      },
    };
  };
  try { return vi.ancestorClipped(el, targetRect); }
  finally { global.document = savedDocument; global.getComputedStyle = savedGetComputedStyle; }
}
const CLIP_PARENT = { left: 0, right: 500, top: 0, bottom: 100 };
const BELOW_BUT_X_OVERLAPS = { left: 20, right: 80, top: 300, bottom: 330 };
const RIGHT_BUT_Y_OVERLAPS = { left: 600, right: 680, top: 20, bottom: 50 };
ok(ancestorClip("clip", "visible", CLIP_PARENT, BELOW_BUT_X_OVERLAPS) === false, "x-only clip ignores y separation");
ok(ancestorClip("visible", "clip", CLIP_PARENT, BELOW_BUT_X_OVERLAPS) === true, "y clip rejects y separation");
ok(ancestorClip("clip", "visible", CLIP_PARENT, RIGHT_BUT_Y_OVERLAPS) === true, "x clip rejects x separation");
ok(ancestorClip("hidden", "auto", CLIP_PARENT, BELOW_BUT_X_OVERLAPS) === true, "computed hidden-auto clips y scrollport");
ok(ancestorClip("scroll", "visible", CLIP_PARENT, RIGHT_BUT_Y_OVERLAPS) === true, "scroll clips x separation");
ok(ancestorClip("overlay", "visible", CLIP_PARENT, RIGHT_BUT_Y_OVERLAPS) === true, "legacy overlay clips x separation");
// ── batch-8 ITEM 2/3: content-visibility:hidden + off-box text-indent ──
ok(vi.isRendered(rec({ contentVisibilityHidden: true })) === false, "content-visibility hidden");
ok(vi.isRendered(rec({ textIndentHidden: true })) === false, "text-indent off-box hidden");
ok(vi.isRendered(rec({ contentVisibilityHidden: false })) === true, "content-visibility unset visible");
ok(vi.isRendered(rec({ textIndentHidden: false })) === true, "modest text-indent visible");
ok(vi.isVisible(rec({ contentVisibilityHidden: true }), { viewport: VP }) === false, "isVisible content-visibility hidden");
ok(vi.isVisible(rec({ textIndentHidden: true }), { viewport: VP }) === false, "isVisible text-indent hidden");
// isVisible composes render-truth
ok(vi.isVisible(rec({ checkVisibility: false }), { viewport: VP }) === false, "isVisible checkVisibility");
ok(vi.isVisible(rec({ clipFullyHidden: true }), { viewport: VP }) === false, "isVisible clip");
ok(vi.isVisible(rec({ checkVisibility: false }), { viewport: VP, requirePaint: false }) === false, "isVisible nopaint render");
// contrast: white-on-white reads no text; distinct grey paints
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255] })) === false, "white on white");
ok(vi.paintsText(rec({ color: [120, 120, 120], effectiveBgColor: [255, 255, 255] })) === true, "grey on white paints");
// ── batch-8 ITEM 5 + batch-9 ITEM 5: contrast skip requires region-level paint
// evidence (a material fraction of the text rect is opaquely covered), not just
// a "paints" flag — a mostly-transparent bg-image with one opaque pixel must
// NOT skip contrast. ──
ok(vi.BG_IMAGE_COVERAGE_FLOOR === 0.1, "BG_IMAGE_COVERAGE_FLOOR");
// honest hero: a real opaque covering photo (>=10% sampled opaque coverage under
// the text rect) paints over the box => skip contrast.
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgImagePaints: true, bgImageOpaqueCoverage: 0.8 })) === true, "painting bg image with coverage skips contrast");
// cheat A: a 1x1/transparent/empty bg-image (effectiveBgIsImage true but no paint
// evidence) must NOT auto-pass invisible white-on-white — contrast still runs.
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgIsImage: true, effectiveBgImagePaints: false })) === false, "contentless bg image still runs contrast");
// cheat B (batch-9): a mostly-transparent bg-image with one opaque pixel —
// effectiveBgImagePaints true but coverage < floor — must run contrast (caught).
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgImagePaints: true, bgImageOpaqueCoverage: 0.01 })) === false, "mostly-transparent bg image runs contrast");
// floor edges:
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgImagePaints: true, bgImageOpaqueCoverage: 0.1 })) === true, "coverage at floor skips contrast");
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgImagePaints: true, bgImageOpaqueCoverage: 0.09 })) === false, "coverage below floor runs contrast");
// absent coverage field (legacy / undecoded) defaults to 0 => contrast runs (never auto-pass):
ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: [255, 255, 255], effectiveBgImagePaints: true })) === false, "paints flag without coverage runs contrast");
ok(vi.paintsText(rec({ colorAlpha: 0.01 })) === false, "alpha floor");
ok(vi.paintsText(rec({ fontSizePx: 4 })) === false, "font floor strict");
ok(vi.paintsText(rec({ fontSizePx: 5 })) === true, "font above floor");
ok(vi.paintsContent(rec({ hasText: false, pseudoHasContent: true })) === true, "pseudo content paints");
ok(vi.contrastRatio([255, 255, 255], [255, 255, 255], 1) < 1.06, "contrast identical ~1");
ok(vi.contrastRatio([0, 0, 0], [255, 255, 255], 1) > 1.06, "contrast black/white high");

// ── batch-7 ITEM 2: deep-quiescence helper (pure) ──
ok(typeof vi.sampleUntilQuiescent === "function", "sampleUntilQuiescent exported");
ok(vi.quiescent([{ ta: "c" }, { ta: "c" }, { ta: "c" }], 3) === true, "quiescent 3 equal");
ok(vi.quiescent([{ ta: "c" }, { ta: "c" }, { ta: "l" }], 3) === false, "quiescent late flip");
ok(vi.quiescent([{ ta: "c" }, { ta: "l" }, { ta: "c" }], 3) === false, "quiescent oscillating");
ok(vi.quiescent([{ ta: "c" }, { ta: "c" }], 3) === false, "quiescent too few frames");

// ── batch-9 ITEM 1: MULTI-POINT, PAINT-AWARE occlusion hit-test ──
// Recreates the /tmp/adv4-occlusion R4 attacker fixtures as a node scene.
// A scene node carries a tag + computed style; elementsFromPoint(x,y) returns
// the stack AT that point so partial occlusion (centre clear, edges covered)
// is representable, and paintsOpaque reads bg-color alpha / opacity / replaced
// tag so a transparent or translucent cover does NOT mark the text blocked.
ok(vi.OPAQUE_ALPHA === 0.5, "OPAQUE_ALPHA");
ok(vi.MATERIAL_OCCLUSION === 0.5, "MATERIAL_OCCLUSION");
ok(typeof vi.hitTestAt === "function", "hitTestAt exported");
// pure verdict helper (mirrored in the python module)
ok(vi.occludedVerdict(0, 9) === "self", "verdict 0/9 self");
ok(vi.occludedVerdict(5, 9) === "blocked", "verdict 5/9 blocked");
ok(vi.occludedVerdict(4, 9) === "self", "verdict 4/9 below material self");
ok(vi.occludedVerdict(0, 0) === null, "verdict no measured points null");

function snode(name, opts) {
  opts = opts || {};
  var s = {
    "background-color": opts.bg || "rgba(0, 0, 0, 0)",
    "opacity": opts.opacity === undefined ? "1" : String(opts.opacity),
    "background-image": opts.bgImage || "none",
    "pointer-events": opts.pe || "auto",
    // batch-11 ITEM 2: position drives effectiveBgColor's visual-stack branch.
    "position": opts.pos || "static",
  };
  var desc = opts.descendants || [];
  var n = { __n: name, nodeType: 1, tagName: (opts.tag || "DIV").toUpperCase(), style: s };
  n.contains = function (o) { return o === n || desc.indexOf(o) >= 0; };
  // batch-11 ITEM 2: optional ancestor chain + rect for effectiveBgColor tests.
  n.parentElement = opts.parent || null;
  if (opts.rect) n.getBoundingClientRect = function () { return opts.rect; };
  return n;
}
function scene(stackFn, fn) {
  var sd = global.document, sg = global.getComputedStyle;
  global.document = { elementsFromPoint: function (x, y) { return stackFn(x, y); } };
  global.getComputedStyle = function (node) {
    var st = (node && node.style) || {};
    return { getPropertyValue: function (p) { return st[p] === undefined ? "" : st[p]; } };
  };
  try { return fn(); } finally { global.document = sd; global.getComputedStyle = sg; }
}
function full(cover) { return function () { return [cover, EL]; }; }
function split(cl, lr, cr, rr) {
  return function (x) {
    if (x >= lr[0] && x < lr[1]) return [cl, EL];
    if (x >= rr[0] && x < rr[1]) return [cr, EL];
    return [EL];
  };
}
var EL = snode("target", { tag: "div" });
function hit(stackFn, r) { return scene(stackFn, function () { return vi.hitTestAt(EL, r, 1280, 800); }); }
// rect shared by the full-cover occlusion fixtures (top40 left40 w400 h60)
var R = { left: 40, top: 40, width: 400, height: 60 };
// rect for the partial fixtures (wider text, top40 left40 w600 h60)
var RW = { left: 40, top: 40, width: 600, height: 60 };

// CHEATS — each BYPASS fixture must now FAIL (blocked):
ok(hit(full(snode("c01", { bg: "rgb(34, 68, 204)" })), R) === "blocked", "01 z-index opaque sibling blocked");
ok(hit(full(snode("c02", { tag: "canvas" })), R) === "blocked", "02 opaque canvas blocked");
ok(hit(split(snode("cl", { bg: "rgb(204, 34, 68)" }), [40, 290], snode("cr", { bg: "rgb(204, 34, 68)" }), [390, 640]), RW) === "blocked", "03 partial centre-clear blocked");
ok(hit(full(snode("c05", { bg: "rgb(255, 255, 255)" })), R) === "blocked", "05 opaque fixed header blocked");
ok(hit(full(snode("c06", { tag: "iframe", bg: "rgb(68, 170, 102)" })), R) === "blocked", "06 iframe overlay blocked");
ok(hit(full(snode("c07", { bg: "rgb(17, 51, 170)", opacity: 0.99 })), R) === "blocked", "07 opacity:0.99 occluder blocked");
ok(hit(split(snode("cl", { bg: "rgb(204, 34, 68)" }), [40, 337], snode("cr", { bg: "rgb(204, 34, 68)" }), [343, 640]), RW) === "blocked", "10 partial near-total blocked");
// /tmp/adv4-gate/impl-10-partial shipped hitTest:self (centre clear) — now blocked.

// HONEST / FALSE-POSITIVE fixtures — must now PASS (self):
ok(hit(full(snode("o04", { bg: "rgba(0, 0, 0, 0)" })), R) === "self", "04 transparent overlay self");
ok(hit(full(snode("h09", { bg: "rgba(255, 255, 255, 0.04)" })), R) === "self", "09 translucent scrim self");
// /tmp/adv4-gate/impl-04-transparent shipped hitTest:blocked — now self.
ok(hit(function () { return [EL]; }, R) === "self", "08 honest pe:none overlay skipped => self");
// own descendant on top is still the element's content:
var CHILD = snode("child", { tag: "span" });
var ELP = snode("parent", { tag: "div", descendants: [CHILD] });
ok(scene(function () { return [CHILD, ELP]; }, function () { return vi.hitTestAt(ELP, R, 1280, 800); }) === "self", "own descendant on top => self");
// transparent ancestor wrapper on top — el shows through:
var WRAP = snode("wrap", { tag: "div", descendants: [EL] });
ok(hit(function () { return [WRAP, EL]; }, R) === "self", "transparent ancestor on top => self");
// false-positive guards preserved: pointer-events:none and off-viewport => null
var ELNONE = snode("elnone", { tag: "div", pe: "none" });
ok(scene(full(snode("cx", { bg: "rgb(0,0,0)" })), function () { return vi.hitTestAt(ELNONE, R, 1280, 800); }) === null, "pointer-events:none => null");
ok(hit(full(snode("cx", { bg: "rgb(0,0,0)" })), { left: -500, top: -500, width: 10, height: 10 }) === null, "all sample points off-viewport => null");

// ── batch-10 ITEM 1: PAINTED-GLYPH-EXTENT occlusion sampling ──
// The occlusion grid now samples the PAINTED TEXT rectangles (Range.
// getClientRects of the element's text node(s)), not the element's full
// bounding box. A short centered heading in a wide block, with opaque
// decorative rules flanking the EMPTY box area, is read THROUGH the glyphs —
// not the empty flanks — closing the R5 F2 false-positive that the bounding-box
// grid produced. Recreates the R5 occlusion fixtures as node scenes: a glyph
// rect distinct from the bounding box, with elementsFromPoint painting opaque
// flanks OUTSIDE the glyph extent.
ok(typeof vi.glyphRectsOf === "function", "glyphRectsOf exported");
ok(typeof vi.samplePointsForRects === "function", "samplePointsForRects exported");
ok(typeof vi.unionRect === "function", "unionRect exported");
// pure: the grid distributes a 5x3 block across EACH rect of a multi-line run
ok(vi.samplePointsForRects([{ left: 0, top: 0, width: 100, height: 30 }], 1280, 800).length === 15, "one glyph rect => 15 points");
ok(vi.samplePointsForRects([{ left: 0, top: 0, width: 100, height: 30 }, { left: 0, top: 40, width: 100, height: 30 }], 1280, 800).length === 30, "two glyph rects => 30 points");
ok(vi.unionRect([{ left: 10, top: 10, width: 20, height: 20 }, { left: 50, top: 10, width: 20, height: 20 }]).width === 60, "unionRect spans both rects");
ok(vi.unionRect([]) === null, "unionRect empty => null");

// glyph-aware scene: EL carries a text node; document.createRange returns the
// painted glyph rect; elementsFromPoint paints opaque flanks/covers by x.
function gscene(glyphRects, stackFn, fn) {
  var sd = global.document, sg = global.getComputedStyle;
  global.document = {
    elementsFromPoint: function (x, y) { return stackFn(x, y); },
    createRange: function () {
      return {
        selectNodeContents: function () {},
        getClientRects: function () { return glyphRects; },
      };
    },
  };
  global.getComputedStyle = function (node) {
    var st = (node && node.style) || {};
    return { getPropertyValue: function (p) { return st[p] === undefined ? "" : st[p]; } };
  };
  try { return fn(); } finally { global.document = sd; global.getComputedStyle = sg; }
}
function textEl(name) {
  var n = snode(name, { tag: "h2" });
  n.childNodes = [{ nodeType: 3, nodeValue: "EAT REAL" }];
  return n;
}

// F2 (must PASS / self): wide box [50..850]; opaque rules flank [<370] and
// [>530]; glyphs centered in the clear gap [370..530]. The OLD bounding-box
// grid read 4/5 columns blocked (the flanks) => blocked (the FP). The glyph
// grid samples only the clear gap => self.
var F2 = textEl("f2");
var F2L = snode("ruleL", { bg: "rgb(51, 68, 85)" });
var F2R = snode("ruleR", { bg: "rgb(51, 68, 85)" });
function f2Stack(x) {
  if (x < 370) return [F2L, F2];
  if (x > 530) return [F2R, F2];
  return [F2];
}
ok(gscene([{ left: 370, top: 40, width: 160, height: 70 }], f2Stack,
  function () { return vi.hitTestAt(F2, { left: 50, top: 40, width: 800, height: 70 }, 1280, 800); }) === "self",
  "F2 short centered glyphs flanked by opaque rules read self (R5 FP fixed)");

// B1 (must FAIL / blocked): long glyphs fill the box [40..640]; two opaque
// covers leave only a ~6px center strip clear. >= 50% of glyph points blocked.
var B1 = textEl("b1");
var B1L = snode("coverL", { bg: "rgb(204, 34, 68)" });
var B1R = snode("coverR", { bg: "rgb(204, 34, 68)" });
function b1Stack(x) {
  if (x >= 40 && x < 337) return [B1L, B1];
  if (x >= 343 && x <= 640) return [B1R, B1];
  return [B1];
}
ok(gscene([{ left: 40, top: 40, width: 600, height: 60 }], b1Stack,
  function () { return vi.hitTestAt(B1, { left: 40, top: 40, width: 600, height: 60 }, 1280, 800); }) === "blocked",
  "B1 95%-covered glyphs read blocked (bypass detection preserved)");

// B2 / occluded-label (must FAIL / blocked): a fully opaque panel over the
// entire heading — the user sees the panel, not the text.
var B2 = textEl("b2");
var B2C = snode("cover", { bg: "rgb(255, 255, 255)" });
ok(gscene([{ left: 40, top: 40, width: 600, height: 60 }], function () { return [B2C, B2]; },
  function () { return vi.hitTestAt(B2, { left: 40, top: 40, width: 600, height: 60 }, 1280, 800); }) === "blocked",
  "B2 full opaque cover reads blocked (occluded-label preserved)");

// fallback: an element with no resolvable text nodes (no glyph rects) keeps the
// bounding-box behaviour — a control occluder over the box still reads blocked.
ok(hit(full(snode("nb", { bg: "rgb(0,0,0)" })), R) === "blocked", "no-text element falls back to bounding-box grid");

// ── batch-11 ITEM 2: visual-stack effectiveBgColor for fixed/absolute/sticky ──
// A position:fixed nav's label is NOT a DOM descendant of the dark section it
// visually overlaps, so the parentElement walk reaches <body> cream and a
// white-inverted label reads white-on-cream (invisible) — false-failing
// state-reveal. For an out-of-flow context, effectiveBgColor now reads the
// VISUAL STACK at the element centre and returns the first opaque painting
// node's bg colour (the dark section showing through), so white-on-dark paints.
ok(typeof vi.effectiveBgColor === "function", "effectiveBgColor exported");
(function () {
  var DARK = snode("section", { bg: "rgb(17, 0, 0)" });            // dga_dark behind the pill
  var LABEL = snode("label", { bg: "rgba(0, 0, 0, 0)",
    rect: { left: 600, top: 10, width: 80, height: 20 } });
  var PILL = snode("pill", { bg: "rgba(0, 0, 0, 0)", pos: "fixed",  // translucent backdrop-blur
    descendants: [LABEL] });
  LABEL.parentElement = PILL;
  var bg = scene(function () { return [LABEL, PILL, DARK]; },
    function () { return vi.effectiveBgColor(LABEL); });
  ok(bg && bg[0] === 17 && bg[1] === 0 && bg[2] === 0,
    "fixed-context label over dark visual stack reads dark bg (not body cream)");
  // and the corrected backdrop makes a white inverted label PAINT
  ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: bg })) === true,
    "white inverted label over dark visual stack paints text");
})();
// GUARD: an in-flow (static) element whose ancestor paints an opaque bg uses the
// DOM-ancestor walk, NOT the visual stack — so the visual-stack branch cannot
// change the answer for the common case (no regression / no false reads).
(function () {
  var BODY = snode("body", { bg: "rgb(245, 239, 228)" });          // cream
  var EL2 = snode("static-label", { bg: "rgba(0, 0, 0, 0)",
    rect: { left: 10, top: 10, width: 40, height: 20 } });
  EL2.parentElement = BODY;
  var darkDecoy = snode("decoy", { bg: "rgb(0, 0, 0)" });          // behind, but must be ignored
  var bg = scene(function () { return [EL2, darkDecoy]; },
    function () { return vi.effectiveBgColor(EL2); });
  ok(bg && bg[0] === 245 && bg[1] === 239 && bg[2] === 228,
    "in-flow element uses DOM-ancestor bg (cream), not the visual stack");
})();
// GUARD: a fixed element sitting on its OWN opaque bg returns that bg (a solid
// fixed header is its own backdrop) — visual stack stops at the first opaque.
(function () {
  var DARK = snode("section", { bg: "rgb(17, 0, 0)" });
  var SOLID = snode("solid-header", { bg: "rgb(10, 20, 30)", pos: "fixed",
    rect: { left: 0, top: 0, width: 1280, height: 60 } });
  var bg = scene(function () { return [SOLID, DARK]; },
    function () { return vi.effectiveBgColor(SOLID); });
  ok(bg && bg[0] === 10 && bg[1] === 20 && bg[2] === 30,
    "fixed element with its own opaque bg returns that bg, not the layer behind");
})();

// ── tools-batch-12 ITEM 1: MULTI-POINT visual-stack effectiveBgColor ──
// A single centre sample misjudges text that spans a region. An out-of-flow
// white-inverted label whose CENTRE happens to sit over a light strip (a logo
// tile, a lighter card behind the nav) but whose glyphs mostly sit over the dark
// section must still read the DARK majority backdrop and PAINT — the old single
// centre sample read the light strip and false-failed. Sampling the glyph extent
// and taking the modal backdrop fixes it.
(function () {
  var DARKSEC = snode("darksec", { bg: "rgb(20, 10, 10)" });             // the dark section behind the bulk
  var LIGHTSTRIP = snode("lightstrip", { bg: "rgb(250, 248, 245)" });     // an opaque light tile under the CENTRE only
  var LBL = snode("mp-label", { tag: "h2", bg: "rgba(0, 0, 0, 0)",
    rect: { left: 100, top: 40, width: 300, height: 30 } });
  LBL.childNodes = [{ nodeType: 3, nodeValue: "EAT REAL FOOD" }];
  var PILL2 = snode("mp-pill", { bg: "rgba(0, 0, 0, 0)", pos: "fixed", descendants: [LBL] });
  LBL.parentElement = PILL2;
  // glyph cols at x = 130,190,250,310,370 — only the centre column (x in [230,270])
  // hits the light strip; the other four columns hit the dark section.
  function centreLightStack(x) {
    if (x >= 230 && x <= 270) return [LBL, PILL2, LIGHTSTRIP];
    return [LBL, PILL2, DARKSEC];
  }
  var bgM = gscene([{ left: 100, top: 40, width: 300, height: 30 }], centreLightStack,
    function () { return vi.effectiveBgColor(LBL); });
  ok(bgM && bgM[0] === 20 && bgM[1] === 10 && bgM[2] === 10,
    "multi-point: label whose CENTRE sample hits a light strip reads the DARK majority backdrop");
  ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: bgM })) === true,
    "white inverted label over majority-dark visual stack paints (single-centre would false-fail)");
})();
// GUARD (detection preserved): a label over a UNIFORMLY light backdrop reads the
// light bg at every glyph sample, so near-white text over it stays INVISIBLE —
// the multi-point change must not mint a false PASS for light-on-light.
(function () {
  var WHITE = snode("white-bd", { bg: "rgb(255, 255, 255)" });
  var LBL2 = snode("mp-invis", { tag: "h2", bg: "rgba(0, 0, 0, 0)",
    rect: { left: 100, top: 40, width: 300, height: 30 } });
  LBL2.childNodes = [{ nodeType: 3, nodeValue: "INVISIBLE" }];
  var PILL3 = snode("mp-pill3", { bg: "rgba(0, 0, 0, 0)", pos: "fixed", descendants: [LBL2] });
  LBL2.parentElement = PILL3;
  var bgL = gscene([{ left: 100, top: 40, width: 300, height: 30 }],
    function () { return [LBL2, PILL3, WHITE]; },
    function () { return vi.effectiveBgColor(LBL2); });
  ok(bgL && bgL[0] === 255 && bgL[1] === 255 && bgL[2] === 255,
    "multi-point: label over a uniformly light backdrop reads the light bg");
  ok(vi.paintsText(rec({ color: [255, 255, 255], effectiveBgColor: bgL })) === false,
    "white text over a uniformly white multi-point backdrop still FAILS contrast (invisible)");
})();

// R5 honest controls routed through the GLYPH sampler (must PASS / self): a
// single-sided cover over LESS than half the glyph rect leaves the majority of
// the painted text readable (F5/F7), and a heading with no occluder over its
// glyphs is fully readable (F9 word-reveal). These exercise the new sampling
// path itself, not just the shared occludedVerdict threshold.
var F5 = textEl("f5");
var F5C = snode("f5cover", { bg: "rgb(34, 34, 34)" });
function f5Stack(x) { return x < 280 ? [F5C, F5] : [F5]; } // covers ~40% of [40..640]
ok(gscene([{ left: 40, top: 40, width: 600, height: 60 }], f5Stack,
  function () { return vi.hitTestAt(F5, { left: 40, top: 40, width: 600, height: 60 }, 1280, 800); }) === "self",
  "F5/F7 single-sided <50% glyph cover reads self");
var F9 = textEl("f9");
ok(gscene([{ left: 40, top: 40, width: 600, height: 60 }], function () { return [F9]; },
  function () { return vi.hitTestAt(F9, { left: 40, top: 40, width: 600, height: 60 }, 1280, 800); }) === "self",
  "F9 no-occluder word-reveal reads self");

// ── batch-10 ITEM 3: POSITIONALLY-CORRECT bg-image opaque coverage ──
// bgImageOpaqueCoverage decoded into a fixed canvas and counted ALL alpha
// pixels globally, ignoring the element rect, background-position and
// background-size — opaque pixels ELSEWHERE in the asset skipped contrast for
// text over a TRANSPARENT region (and vice versa). The render rect is now
// computed from background-size/position; only pixels under the mapped text
// region are counted. bgImageRenderRect is the pure placement helper.
ok(typeof vi.bgImageRenderRect === "function", "bgImageRenderRect exported");
ok(typeof vi.bgRenderDims === "function", "bgRenderDims exported");
(function () {
  // cover: a 100x100 image in a 400x200 box scales by max(4,2)=4 => 400x400.
  var rr = vi.bgImageRenderRect({ left: 0, top: 0, width: 400, height: 200 }, 100, 100, "cover", "0% 0%");
  ok(rr.width === 400 && rr.height === 400 && rr.left === 0 && rr.top === 0, "cover scales by max ratio");
})();
(function () {
  // contain + center: a 200x100 image in 400x200 scales by min(2,2)=2 => 400x200, centered.
  var rr = vi.bgImageRenderRect({ left: 50, top: 30, width: 400, height: 200 }, 200, 100, "contain", "center center");
  ok(rr.width === 400 && rr.height === 200 && rr.left === 50 && rr.top === 30, "contain centers in box (viewport coords)");
})();
(function () {
  // explicit px size + px position, box offset honored.
  var rr = vi.bgImageRenderRect({ left: 50, top: 30, width: 400, height: 200 }, 200, 100, "100px 50px", "10px 20px");
  ok(rr.left === 60 && rr.top === 50 && rr.width === 100 && rr.height === 50, "explicit px size + px position");
})();
(function () {
  // 100% position pins the image's right/bottom edge to the box's.
  var rr = vi.bgImageRenderRect({ left: 0, top: 0, width: 400, height: 200 }, 200, 100, "200px 100px", "100% 100%");
  ok(rr.left === 200 && rr.top === 100, "100% position pins to right/bottom");
})();

// canvas-mocked integration: a region over an OPAQUE zone of the drawn canvas
// reports high coverage; over a TRANSPARENT zone reports ~0; off-box => 0; no
// image => null. Proves the region->canvas sub-rect mapping + coverage scaling.
function imgScene(alphaAt, fn) {
  var sd = global.document, sg = global.getComputedStyle, si = global.Image;
  global.Image = function () { this.complete = true; this.naturalWidth = 200; this.naturalHeight = 200; this.src = ""; };
  global.getComputedStyle = function (node) {
    var st = (node && node.style) || {};
    return { getPropertyValue: function (p) { return st[p] === undefined ? "" : st[p]; } };
  };
  global.document = {
    createElement: function (t) {
      if (t !== "canvas") return {};
      var st = { width: 0, height: 0 };
      return {
        set width(v) { st.width = v; }, get width() { return st.width; },
        set height(v) { st.height = v; }, get height() { return st.height; },
        getContext: function () {
          return {
            drawImage: function () {},
            getImageData: function (x, y, w, h) {
              var data = [];
              for (var j = 0; j < h; j++) {
                for (var i = 0; i < w; i++) { data.push(0, 0, 0, alphaAt(x + i, y + j)); }
              }
              return { data: data };
            },
          };
        },
      };
    },
  };
  try { return fn(); } finally { global.document = sd; global.getComputedStyle = sg; global.Image = si; }
}
function bgEl(box) {
  var n = snode("bg", { tag: "div", bgImage: "url(x.png)" });
  n.nodeType = 1; // real elements are ELEMENT_NODE; the coverage walk gates on it
  n.style["background-size"] = "cover";
  n.style["background-position"] = "0% 0%";
  n.style["background-repeat"] = "no-repeat";
  n.getBoundingClientRect = function () { return box; };
  return n;
}
var IBOX = { left: 0, top: 0, width: 640, height: 200 };
function halfOpaque(cx) { return cx < 32 ? 255 : 0; } // 64-wide canvas: left opaque, right clear
ok(imgScene(halfOpaque, function () {
  return vi.bgImageOpaqueCoverage(bgEl(IBOX), { left: 0, top: 0, width: 160, height: 200 });
}) >= 0.9, "region over opaque image zone => high coverage");
ok(imgScene(halfOpaque, function () {
  return vi.bgImageOpaqueCoverage(bgEl(IBOX), { left: 480, top: 0, width: 160, height: 200 });
}) <= 0.1, "region over transparent image zone => ~0 coverage (contrast not skipped)");
ok(imgScene(function () { return 255; }, function () {
  return vi.bgImageOpaqueCoverage(bgEl(IBOX), { left: 2000, top: 0, width: 100, height: 100 });
}) === 0, "region off the bg box => 0 coverage");

// ── batch-10 ITEM 2: route bg-image occlusion through opaque coverage ──
// paintsOpaque treated ANY topmost foreign node with a url() background-image as
// opaque, so a decorative PNG with a TRANSPARENT region over readable text
// false-occluded it. It now measures the image's opaque coverage over the glyph
// region (ITEM 3's positional sampler): below the floor => not occluding (the
// text shows through); unmeasurable coverage (cross-origin/undecoded/no canvas)
// stays CONSERVATIVELY opaque so occlusion bypass detection is NOT loosened.
ok(typeof vi.bgImageOccludes === "function", "bgImageOccludes exported");
ok(vi.bgImageOccludes(null) === true, "null coverage => conservative opaque (bypass preserved)");
ok(vi.bgImageOccludes(undefined) === true, "absent coverage => conservative opaque");
ok(vi.bgImageOccludes(0) === false, "0 coverage => transparent, not occluding");
ok(vi.bgImageOccludes(0.4) === false, "below OPAQUE_ALPHA => not occluding");
ok(vi.bgImageOccludes(0.5) === true, "at OPAQUE_ALPHA => occluding");
ok(vi.bgImageOccludes(0.9) === true, "high coverage => occluding");

// unmeasurable bg-image cover (the plain scene has no canvas) stays blocked —
// the blanket-opaque behaviour is preserved where coverage cannot be sampled, so
// a genuine opaque cover is never let through.
var IMGCOVER = snode("imgcover", { tag: "div", bgImage: "url(c.png)" });
IMGCOVER.nodeType = 1;
IMGCOVER.getBoundingClientRect = function () { return R; };
ok(hit(full(IMGCOVER), R) === "blocked", "bg-image cover with unmeasurable coverage stays blocked");

// integration: a foreign bg-image cover topmost over the glyphs. TRANSPARENT
// over the glyph region => not occluding => self; OPAQUE => blocked.
var OEL = textEl("oel");
var OGLYPH = [{ left: 40, top: 40, width: 600, height: 60 }];
function occImgScene(coverNode, alphaAt, fn) {
  var sd = global.document, sg = global.getComputedStyle, si = global.Image;
  global.Image = function () { this.complete = true; this.naturalWidth = 200; this.naturalHeight = 200; this.src = ""; };
  global.getComputedStyle = function (node) {
    var st = (node && node.style) || {};
    return { getPropertyValue: function (p) { return st[p] === undefined ? "" : st[p]; } };
  };
  global.document = {
    elementsFromPoint: function () { return [coverNode, OEL]; },
    createRange: function () { return { selectNodeContents: function () {}, getClientRects: function () { return OGLYPH; } }; },
    createElement: function (t) {
      if (t !== "canvas") return {};
      var stt = { width: 0, height: 0 };
      return {
        set width(v) { stt.width = v; }, get width() { return stt.width; },
        set height(v) { stt.height = v; }, get height() { return stt.height; },
        getContext: function () {
          return {
            drawImage: function () {},
            getImageData: function (x, y, w, h) {
              var d = [];
              for (var j = 0; j < h; j++) {
                for (var i = 0; i < w; i++) { d.push(0, 0, 0, alphaAt(x + i, y + j)); }
              }
              return { data: d };
            },
          };
        },
      };
    },
  };
  try { return fn(); } finally { global.document = sd; global.getComputedStyle = sg; global.Image = si; }
}
function mkImgCover(box) {
  var n = snode("c", { tag: "div", bgImage: "url(c.png)" });
  n.nodeType = 1;
  n.style["background-size"] = "cover";
  n.style["background-position"] = "0% 0%";
  n.style["background-repeat"] = "no-repeat";
  n.getBoundingClientRect = function () { return box; };
  return n;
}
var COVERBOX = { left: 40, top: 40, width: 600, height: 60 };
ok(occImgScene(mkImgCover(COVERBOX), function () { return 0; },
  function () { return vi.hitTestAt(OEL, COVERBOX, 1280, 800); }) === "self",
  "transparent-region PNG over text reads self (R5 ITEM 2 FP fixed)");
ok(occImgScene(mkImgCover(COVERBOX), function () { return 255; },
  function () { return vi.hitTestAt(OEL, COVERBOX, 1280, 800); }) === "blocked",
  "opaque PNG over text reads blocked (occlusion preserved)");

// ── batch-8 ITEM 4: settle horizon — injectable clock/raf/rand; a defect that
// flips AFTER the old fixed 12s cap must still land in the recorded series, and
// an honest never-changing series must still quiesce. Drives the loop with a
// virtual clock so the assertions are deterministic and fast. ──
(async () => {
  let vnow = 0;
  const fakeRaf = (cb) => { vnow += 1000; cb(); };
  const collect = () => ({ ta: vnow >= 13000 ? "left" : "center" });
  const series = await vi.sampleUntilQuiescent(collect, {
    frames: 3, floorMs: 8000, maxMs: 18000, now: () => vnow, raf: fakeRaf, rand: () => 0.99,
  });
  ok(series.some(s => s.ta === "left"), "late flip past old 12s cap recorded in series");
  ok(vi.settledValue(series).ta === "left", "settledValue exposes the late defect, not the transient");
  let hnow = 0;
  const hRaf = (cb) => { hnow += 1000; cb(); };
  const honest = await vi.sampleUntilQuiescent(() => ({ ta: "center" }), {
    frames: 3, floorMs: 8000, maxMs: 18000, now: () => hnow, raf: hRaf, rand: () => 0.0,
  });
  ok(vi.quiescent(honest, 3) === true, "honest series quiesces (no false fail)");
  ok(vi.settledValue(honest).ta === "center", "honest settled value center");
})().then(() => {
  if (failed > 0) { console.error(failed + " assertion(s) failed"); process.exit(1); }
  console.log("all visible-identity.js assertions passed");
}).catch((e) => { console.error("FAIL: settle-horizon " + e); process.exit(1); });
"""


def test_pure_functions_match_python_mirror(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node required to exercise the browser eval")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(
        ["node", str(harness), str(LIB)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
