"""D1 (loop-nvti-0): extract-section-map.sh enumerated 5 sections on a 23k-px
page — ~19.4k px (five of the six section.js-nav-section elements, each taller
than 2x viewport) silently vanished into one catch-all. Root cause: semantic
containers taller than 2x viewport are DECOMPOSED into children, but when the
recursion collects nothing (children are runtime-built/collapsed at capture
time — GSAP page-stacks), the section is dropped entirely instead of being
kept wholesale. Decompose-don't-drop: an unproductive decomposition must fall
back to the section itself.

The enumeration JS lives in a heredoc (cat <<JSEOF > tmpfile ...
JSEOF). These tests extract it and run it under node with a minimal DOM stub —
no browser."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "extract-section-map.sh")

DOM_STUB = r"""
const VIEWPORT_H = 900;
function el(tag, opts = {}) {
  const e = {
    tagName: tag.toUpperCase(),
    children: [],
    className: opts.className || '',
    id: opts.id || null,
    textContent: opts.text !== undefined ? opts.text : 'x',
    parentElement: null,
    _rect: {
      top: opts.top || 0,
      left: opts.left || 0,
      width: opts.width === undefined ? 1440 : opts.width,
      height: opts.height || 0
    },
    _attrs: opts.attrs || {},
    _position: opts.position || 'static',
    _display: opts.display || 'block',
    _visibility: opts.visibility || 'visible',
    _opacity: opts.opacity === undefined ? '1' : String(opts.opacity),
    getBoundingClientRect() {
      return {
        top: this._rect.top,
        left: this._rect.left,
        width: this._rect.width,
        height: this._rect.height
      };
    },
    getAttribute(name) { return this._attrs[name] || null; },
    querySelector(_sel) {
      // minimal: report a media descendant only when a child tag matches
      const MEDIA = ['IMG','VIDEO','CANVAS','IFRAME','SVG','PICTURE','OBJECT','EMBED'];
      const walk = (n) => n.children.find(c => MEDIA.includes(c.tagName) || walk(c)) || null;
      return walk(this);
    },
    querySelectorAll(_sel) {
      const MEDIA = ['IMG','VIDEO','CANVAS','IFRAME','SVG','PICTURE','OBJECT','EMBED'];
      const out = [];
      const walk = (n) => {
        n.children.forEach(c => {
          if (MEDIA.includes(c.tagName)) out.push(c);
          walk(c);
        });
      };
      walk(this);
      return out;
    },
    contains(other) {
      if (other === this) return true;
      return this.children.some(c => c.contains(other));
    },
  };
  return e;
}
function add(parent, child) { parent.children.push(child); child.parentElement = parent; return child; }

// nvti shape: header + tech-hero + 6x section.js-nav-section + footer.
// js-nav 2..6 are taller than 2x viewport and their inner content is
// runtime-built (single small div at capture time) so decomposition finds
// nothing inside them.
const body = el('body', { height: 23220 });
add(body, el('header', { className: 'header', height: 64, top: 0, position: 'fixed' }));
const hero = add(body, el('div', { className: 'tech-hero ready', height: 2925, top: 0 }));
add(hero, el('div', { className: 'sticky', height: 2925, top: 0 }));
const heights = [1219, 4019, 4019, 4019, 4019, 3235];
let y = 2025;
for (const h of heights) {
  const s = add(body, el('section', { className: 'js-nav-section', height: h, top: y }));
  add(s, el('div', { className: 'container__inner', height: 80, top: y }));
  y += h;
}
add(body, el('footer', { className: 'footer js-parallax-section', height: 569, top: 22651 }));

global.document = { body };
global.window = { innerHeight: VIEWPORT_H, scrollY: 0 };
global.getComputedStyle = (e) => ({
  position: e._position || 'static',
  display: e._display || 'block',
  visibility: e._visibility || 'visible',
  opacity: e._opacity === undefined ? '1' : e._opacity,
});
"""


def _run_enumeration(stub_extra: str = "") -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    src = SCRIPT.read_text(encoding="utf-8")
    marker = "cat <<'JSEOF' > "
    start = src.index(chr(10), src.index(marker)) + 1
    end = src.index("\nJSEOF")
    eval_js = src[start:end]
    harness = DOM_STUB + stub_extra + f"\nconst out = {eval_js.strip()};\nconsole.log(out);\n"
    proc = subprocess.run(["node", "-e", harness],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data: dict = json.loads(proc.stdout)
    return data


def test_tall_semantic_sections_survive_unproductive_decomposition() -> None:
    result = _run_enumeration()
    js_nav = [s for s in result["sections"] if "js-nav-section" in (s["className"] or "")]
    assert len(js_nav) == 6, (
        f"expected all 6 js-nav-section rows, got {len(js_nav)} — tall sections "
        f"whose decomposition collects nothing must fall back to themselves; "
        f"sections={[(s['tag'], s['className'], s['height']) for s in result['sections']]}"
    )


def test_header_footer_hero_still_enumerated() -> None:
    result = _run_enumeration()
    classes = " | ".join((s["className"] or "") for s in result["sections"])
    assert result["hasHeader"] and result["hasFooter"]
    assert "tech-hero" in classes


def test_coverage_stats_reported() -> None:
    # D1 companion: the artifact must expose how much of the page the
    # enumeration covered, so a silent 19.4k-px gap can never hide again.
    result = _run_enumeration()
    assert "docHeight" in result and "coveredPx" in result, (
        "section-map artifact must report docHeight/coveredPx coverage stats"
    )


def test_content_empty_rails_are_never_sections() -> None:
    """L-CAP-3 (loop-ebpb-0/1): six invisible, classless, childless 1350px
    rail divs enumerated as sections and poisoned the compare/geometry
    denominators with 12 phantom rows across two loops. A div with no element
    children, no text, and no media is decoration, not a section."""
    stub_extra = """
const rail = add(body, el('div', { className: '', height: 8100, top: 0 }));
for (let i = 0; i < 6; i++) {
  add(rail, el('div', { className: '', height: 1350, top: i * 1350, text: '' }));
}
"""
    result = _run_enumeration(stub_extra)
    empties = [s for s in result["sections"]
               if not (s["className"] or "").strip() and s["childCount"] == 0]
    assert empties == [], (
        f"content-empty rail divs must never enumerate: {empties}"
    )


def test_section_map_reports_visible_media_evidence() -> None:
    stub_extra = """
body.children = [];
body._rect.height = 800;
const mediaSection = add(body, el('section', {
  className: 'single-media',
  height: 600,
  top: 0,
  text: ''
}));
add(mediaSection, el('video', { height: 320, top: 40, text: '' }));
"""
    result = _run_enumeration(stub_extra)
    row = next(s for s in result["sections"] if s["className"] == "single-media")
    assert row["childCount"] == 1
    assert row["hasVisibleMedia"] is True
    assert row["visibleMediaCount"] == 1


def test_section_map_ignores_hidden_media_evidence() -> None:
    stub_extra = """
body.children = [];
body._rect.height = 800;
const hiddenMediaSection = add(body, el('section', {
  className: 'hidden-media',
  height: 600,
  top: 0,
  text: ''
}));
add(hiddenMediaSection, el('video', {
  height: 320,
  top: 40,
  text: '',
  visibility: 'hidden'
}));
add(hiddenMediaSection, el('img', {
  height: 0,
  width: 320,
  top: 40,
  text: ''
}));
"""
    result = _run_enumeration(stub_extra)
    row = next(s for s in result["sections"] if s["className"] == "hidden-media")
    assert row["childCount"] == 2
    assert row["hasVisibleMedia"] is False
    assert row["visibleMediaCount"] == 0


def test_absolute_overlay_child_does_not_split_composite() -> None:
    """FIX-2: a video-player composite — container(relative) holding a player
    (in-flow, relative) and its control overlay (ABSOLUTE, same rect) — must
    enumerate as ONE section (the container). Counting the absolute overlay as a
    'big div child' made the container look like a 2-section stack, descended
    into it, and emitted player + control as two sibling sections so the control
    no longer overlaid the video. An out-of-flow child must not trigger descent."""
    stub_extra = """
const cont = add(body, el('div', { className: 'style_container__gnBIP', height: 800, top: 0, position: 'relative' }));
add(cont, el('div', { className: 'style_playerWrapper__l50MG', height: 774, top: 0, position: 'relative' }));
add(cont, el('div', { className: 'style_controlWrapper__ICw7', height: 772, top: 0, position: 'absolute' }));
"""
    result = _run_enumeration(stub_extra)
    classes = [(s["className"] or "") for s in result["sections"]]
    # The composite is ONE section (the container); no separate player/control rows.
    assert any("style_container__gnBIP" in c for c in classes), (
        f"the player composite container must be a section; got {classes}")
    assert not any("playerWrapper" in c for c in classes), (
        f"player must NOT be split into its own section; got {classes}")
    assert not any("controlWrapper" in c for c in classes), (
        f"absolute control overlay must NOT be split into its own section; got {classes}")


def test_parallel_columns_do_not_split_composite() -> None:
    """Two large in-flow children on the same row form one composite section.

    eBay Playbook uses this shape for media grids and video blurbs. Treating
    the columns as independent sections drops their shared grid wrapper, so the
    generated clone stacks them vertically and inflates the document height.
    """
    stub_extra = """
body.children = [];
body._rect.height = 800;
const composite = add(body, el('div', {
  className: 'style_video_blurb__ldcJv',
  height: 800,
  top: 0,
  position: 'relative',
}));
const copy = add(composite, el('div', {
  className: 'style_blurb__EpnZa',
  height: 780,
  top: 0,
}));
add(copy, el('p', { height: 120, top: 20, text: 'Design system copy' }));
const media = add(composite, el('div', {
  className: 'style_slideshow__7xln1',
  height: 780,
  top: 0,
}));
add(media, el('img', { height: 760, top: 10 }));
"""
    result = _run_enumeration(stub_extra)
    classes = [section["className"] or "" for section in result["sections"]]

    assert classes == ["style_video_blurb__ldcJv"], (
        f"parallel columns must retain their shared section wrapper; got {classes}"
    )


def test_vertically_distinct_large_children_still_split() -> None:
    """The opaque-div fallback must still decompose a real vertical stack."""
    stub_extra = """
body.children = [];
body._rect.height = 1600;
const stack = add(body, el('div', {
  className: 'opaque-page-stack',
  height: 1600,
  top: 0,
}));
const first = add(stack, el('div', {
  className: 'first-panel',
  height: 800,
  top: 0,
}));
add(first, el('p', { height: 100, top: 20, text: 'First panel' }));
const second = add(stack, el('div', {
  className: 'second-panel',
  height: 800,
  top: 800,
}));
add(second, el('p', { height: 100, top: 820, text: 'Second panel' }));
"""
    result = _run_enumeration(stub_extra)
    classes = [section["className"] or "" for section in result["sections"]]

    assert classes == ["first-panel", "second-panel"], (
        f"vertically distinct panels must remain separate sections; got {classes}"
    )


def test_contentful_classless_div_still_enumerates() -> None:
    # Guard: opaque-hashed-class sites legitimately have classless wrappers
    # WITH content — only content-EMPTY divs are filtered.
    stub_extra = """
const bare = add(body, el('div', { className: '', height: 700, top: 23220 }));
add(bare, el('div', { className: 'inner', height: 650, top: 23240, text: 'real content' }));
"""
    result = _run_enumeration(stub_extra)
    hits = [s for s in result["sections"] if s["top"] >= 23220]
    assert hits, "a classless div WITH content children must still enumerate"


def test_display_contents_wrapper_exposes_real_landmarks() -> None:
    """Navercorp shape: a zero-height display:contents app wrapper must not
    hide its real header/main/footer children from section enumeration."""
    stub_extra = """
body.children = [];
body._rect.height = 1560;
const contents = add(body, el('div', {
  className: 'app-shell',
  height: 0,
  top: 0,
  display: 'contents',
}));
add(contents, el('header', { className: 'site-header', height: 80, top: 0 }));
add(contents, el('main', { className: 'site-main', height: 1320, top: 80 }));
add(contents, el('footer', { className: 'site-footer', height: 160, top: 1400 }));
"""
    result = _run_enumeration(stub_extra)
    tags = [section["tag"] for section in result["sections"]]
    assert tags == ["header", "main", "footer"], (
        f"display:contents wrapper must expose landmark children; got {tags}"
    )


def test_deep_dominant_framework_root_chain_reaches_section_branch() -> None:
    """docs.github.com shape: several single-dominant-child framework roots
    eventually branch into real landmarks/sections and must be unwrapped."""
    stub_extra = """
body.children = [];
body._rect.height = 2600;
let root = add(body, el('div', {
  className: 'framework-root-0',
  height: 2600,
  top: 0,
}));
for (let i = 1; i <= 3; i++) {
  root = add(root, el('div', {
    className: `framework-root-${i}`,
    height: 2600,
    top: 0,
  }));
}
add(root, el('header', { className: 'docs-header', height: 100, top: 0 }));
const main = add(root, el('main', { className: 'docs-main', height: 2300, top: 100 }));
add(main, el('section', { className: 'docs-intro', height: 900, top: 100 }));
add(main, el('section', { className: 'docs-guides', height: 1400, top: 1000 }));
add(root, el('footer', { className: 'docs-footer', height: 200, top: 2400 }));
"""
    result = _run_enumeration(stub_extra)
    classes = [section["className"] or "" for section in result["sections"]]
    assert classes == [
        "docs-header",
        "docs-intro",
        "docs-guides",
        "docs-footer",
    ], f"deep framework root chain must unwrap to real sections; got {classes}"


def test_single_child_full_bleed_hero_stays_one_section() -> None:
    """A dominant-child chain with no eventual branch is content, not a
    framework wrapper, so the outer full-bleed hero must remain one section."""
    stub_extra = """
body.children = [];
body._rect.height = 900;
const fullBleed = add(body, el('div', {
  className: 'full-bleed-hero',
  height: 900,
  top: 0,
}));
const media = add(fullBleed, el('div', {
  className: 'hero-media',
  height: 900,
  top: 0,
}));
add(media, el('img', { className: 'hero-image', height: 900, top: 0 }));
"""
    result = _run_enumeration(stub_extra)
    classes = [section["className"] or "" for section in result["sections"]]
    assert classes == ["full-bleed-hero"], (
        f"single-child full-bleed hero must remain a leaf section; got {classes}"
    )
