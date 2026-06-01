#!/usr/bin/env bash
# dom-scaffold.sh — produce Phase-4 input grounded in real ref DOM + CSS.
#
# Merges three Phase-2 artifacts into a single scaffold the generator must
# follow verbatim instead of inventing:
#
#   structure.json   tree shape + per-node text (Fix 6 v1)
#   styles.json      per-tag and per-class computed CSS (bg/color/ff/fs/...)
#   section-map.json semantic section enumeration (top/height/cls)
#
# Output: <ref-dir>/dom-scaffold.json
#
# Each top-level section carries:
#   - id, tag, class, bbox (top/height) from section-map
#   - styles dict (resolved against tag + first class lookup in styles.json)
#   - tree[]: array of child-node descriptors with tag/text/class/styles
#
# Why this exists: Design2Code's text-augmented prompting closed most of the
# text-fidelity gap; DCGen's divide-and-conquer per-section closed the layout
# gap. Combining them at the prompt-input layer = front-loaded determinism.
# Phase 4 paste-translates the scaffold; the agent does NOT decide WHAT to
# render — only HOW to express scaffold styles as Tailwind utilities.
#
# arXiv refs:
#   Design2Code (2403.03163) — text-augmented prompting beats screenshot-only
#   DCGen (2406.16386) — per-section descriptions beat full-page descriptions
#   DesignCoder (2506.13663) — structured metadata trees enable repair loops
#
# Usage:
#   dom-scaffold.sh <ref-dir>
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: dom-scaffold.sh <ref-dir>" >&2
  exit 2
fi
REF_DIR="$1"
if [ ! -d "$REF_DIR" ]; then
  echo "dom-scaffold: ref dir not found: $REF_DIR" >&2
  exit 2
fi

STRUCT="$REF_DIR/structure.json"
STYLES="$REF_DIR/styles.json"
SECMAP="$REF_DIR/section-map.json"

for f in "$STRUCT" "$STYLES" "$SECMAP"; do
  if [ ! -f "$f" ]; then
    echo "dom-scaffold: missing input: $f (run Phase 2 first)" >&2
    exit 2
  fi
done

OUT="$REF_DIR/dom-scaffold.json"

python3 - "$STRUCT" "$STYLES" "$SECMAP" "$OUT" <<'PY'
import json
import os
import re
import sys
from pathlib import Path


# Tailwind-relevant style keys to carry through. Drop the rest to keep the
# scaffold lean — Phase 4 doesn't need to look at every computed property.
STYLE_KEYS = (
    "display", "position", "bg", "color", "ff", "fs", "fw", "lh", "ls",
    "padding", "margin", "width", "height",
    # Fidelity-critical props extract-dom.sh captures but the scaffold used to
    # drop — so the generator freehanded shadows / alignment / radius / z-order.
    # ADDITIVE: carried through to Phase 4 so it must reproduce them, not invent.
    "border-radius", "border", "box-shadow", "text-align", "text-transform",
    "white-space", "transform", "opacity", "overflow", "flex", "flex-direction",
    "justify-content", "align-items", "gap", "grid-template-columns",
    "grid-template-rows", "z-index", "min-width", "max-width", "min-height",
    "max-height", "top", "left", "right", "bottom",
)

# Per-node style shortener — mirrors extract-styles.sh's shorten_styles. The
# input is the raw computed-CSS dict extract-dom.sh writes onto each node
# (full property names like 'background-color', 'font-family'); the output
# is the shorthand keyspace dom-scaffold consumers expect. Codex review
# 2026-05-22 (Q1): per-node styles must win over the class-level aggregate
# so exceptional instances (a `.card` inside a hero) don't inherit the
# dominant class's structural layout (320px catalog width stamped over an
# 800px hero card).
_PER_NODE_SHORTHAND = (
    ("display", "display"),
    ("position", "position"),
    ("color", "color"),
    ("font-family", "ff"),
    ("font-size", "fs"),
    ("font-weight", "fw"),
    ("line-height", "lh"),
    ("letter-spacing", "ls"),
    ("padding", "padding"),
    ("margin", "margin"),
    ("width", "width"),
    ("height", "height"),
    # Same fidelity props as STYLE_KEYS — read per-node from structure.json so
    # the exceptional-instance value (this node's real shadow/alignment/radius)
    # wins over the class aggregate (per-node-wins, see walk()). ADDITIVE.
    ("border-radius", "border-radius"),
    ("border", "border"),
    ("box-shadow", "box-shadow"),
    ("text-align", "text-align"),
    ("text-transform", "text-transform"),
    ("white-space", "white-space"),
    ("transform", "transform"),
    ("opacity", "opacity"),
    ("overflow", "overflow"),
    ("flex", "flex"),
    ("flex-direction", "flex-direction"),
    ("justify-content", "justify-content"),
    ("align-items", "align-items"),
    ("gap", "gap"),
    ("grid-template-columns", "grid-template-columns"),
    ("grid-template-rows", "grid-template-rows"),
    ("z-index", "z-index"),
    ("min-width", "min-width"),
    ("max-width", "max-width"),
    ("min-height", "min-height"),
    ("max-height", "max-height"),
    ("top", "top"),
    ("left", "left"),
    ("right", "right"),
    ("bottom", "bottom"),
)
_PER_NODE_NOISE = {"", "normal", "none", "auto", "0px", "rgba(0, 0, 0, 0)"}

# Cheap typography keys (shorthand) carried onto deep text nodes past the
# depth cap. RANK-4 fix: deep leaf text otherwise arrived with no
# fs/fw/color/lh/ls and the generator freehanded typography. These 5 strings
# are cheap; the heavy structural props stay dropped past the cap.
_TYPOGRAPHY_KEYS = {"fs", "fw", "color", "lh", "ls"}


def shorten_node_styles(raw):
    if not isinstance(raw, dict):
        return {}
    out = {}
    bg_image = raw.get("background-image")
    bg_color = raw.get("background-color")
    bg = None
    if isinstance(bg_image, str):
        v = bg_image.strip()
        if v and v not in _PER_NODE_NOISE:
            bg = v
    if bg is None and isinstance(bg_color, str):
        v = bg_color.strip()
        if v and v not in _PER_NODE_NOISE:
            bg = v
    if bg:
        out["bg"] = bg
    for css_name, short in _PER_NODE_SHORTHAND:
        v = raw.get(css_name)
        if isinstance(v, str):
            vv = v.strip()
            if vv and vv not in _PER_NODE_NOISE:
                out[short] = vv
    return out


def resolve_styles(tag, class_str, styles_map):
    """Look up styles.json entries for this node by tag first then by each
    class token. Later lookups override earlier ones (class wins over tag),
    matching CSS cascade in spirit. Returns a flat dict of the STYLE_KEYS
    that were found.
    """
    out = {}
    tag_entry = styles_map.get(tag, {}) if isinstance(styles_map, dict) else {}
    if isinstance(tag_entry, dict):
        for k in STYLE_KEYS:
            v = tag_entry.get(k)
            if v not in (None, ""):
                out[k] = v
    if not class_str:
        return out
    for cls in str(class_str).split():
        if not cls:
            continue
        cls_key = f".{cls}"
        cls_entry = styles_map.get(cls_key, {}) if isinstance(styles_map, dict) else {}
        if isinstance(cls_entry, dict):
            for k in STYLE_KEYS:
                v = cls_entry.get(k)
                if v not in (None, ""):
                    out[k] = v
    return out


def walk(node, styles_map, depth=0, max_depth=8):
    """Walk structure.json into a scaffold-friendly shape: tag/text/class/
    styles/children. Caps STYLE/structure detail past max_depth so massive
    trees don't blow up the prompt — but NEVER drops text. Verbatim text is
    the fidelity ground truth; truncating it makes the generator omit or
    fabricate copy (observed: a flat depth-8 cap dropped ~79% of a deep site's
    text leaves — structure.json had 232 to depth 14, the scaffold kept 49).
    Past the cap, text-bearing nodes are still carried but emitted lightweight
    (tag + text only, no per-node styles dict — the styles are the heavy part,
    not the text).
    """
    if not isinstance(node, dict):
        return None
    tag = node.get("tag", "")
    text = node.get("text", "") or ""
    children = []
    for c in node.get("children", []) or []:
        sub = walk(c, styles_map, depth + 1, max_depth)
        if sub is not None:
            children.append(sub)
    if depth > max_depth:
        # Past the cap: keep the node ONLY if it (or a descendant) carries
        # text; emit it lightweight (tag/text/children) so the deep copy
        # survives without re-bloating the prompt with full style dicts.
        if not text and not children:
            return None
        item = {"tag": tag}
        if text:
            item["text"] = text
            # RANK-4 fix (ADDITIVE): the skill says "copy the measured px/weight
            # for ALL text" — so deep text must carry its real typography, not
            # be freehanded. Attach ONLY the cheap typography subset
            # (fs/fw/color/lh/ls); keep dropping the heavy structural props
            # (border/box-shadow/flex/grid/...) past the cap so the cap still
            # protects the prompt budget.
            type_styles = {
                k: v
                for k, v in shorten_node_styles(node.get("styles") or {}).items()
                if k in _TYPOGRAPHY_KEYS
            }
            if type_styles:
                item["styles"] = type_styles
        if children:
            item["children"] = children
        return item
    cls = (node.get("class") or "")[:80]
    # Per-node styles win over the class/tag aggregate: the aggregate is a
    # fallback for nodes that did not capture a computed value, but the raw
    # per-node styles already reflect the exceptional-instance layout
    # extract-dom.sh measured for *this* node. See Codex review 2026-05-22.
    per_node = shorten_node_styles(node.get("styles") or {})
    aggregate = resolve_styles(tag, cls, styles_map)
    styles = {**aggregate, **per_node}
    item = {"tag": tag}
    if text:
        item["text"] = text
    if cls:
        item["class"] = cls
    if styles:
        item["styles"] = styles
    if children:
        item["children"] = children
    return item


def find_section_node(root, section):
    """Locate the section's OWN root node in structure.json so its per-node
    computed styles can win over the tag/class aggregate (RANK-3 fix). Without
    this, every section descriptor's `styles` came purely from
    resolve_styles(tag, class, ...) — and extract-styles.sh strips structural
    keys (padding/display/margin/width/height) from per-CLASS buckets, leaving
    them only in the per-TAG bucket, so a section's padding/display resolved to
    the page-MODAL value across all elements of that tag (the dominant
    section's padding stamped on every section).

    Match priority: section id (unique) first, then exact tag + class-token
    containment in document order. Returns None when no node matches — callers
    then fall back to the aggregate alone (per-node wins, aggregate fills gaps;
    no behavior is removed).
    """
    sid = (section.get("id") or "").strip()
    s_cls = (section.get("cls") or section.get("className") or "").strip()
    s_tag = section.get("tag") or ""
    s_tokens = set(s_cls.split())

    id_match = [None]
    cls_match = [None]

    def visit(node):
        if not isinstance(node, dict):
            return
        if id_match[0] is None and sid:
            nid = (node.get("id") or "").strip()
            if nid and nid == sid:
                id_match[0] = node
        if cls_match[0] is None and s_tokens:
            n_tokens = set((node.get("class") or "").split())
            if s_tokens <= n_tokens and (not s_tag or node.get("tag") == s_tag):
                cls_match[0] = node
        for c in node.get("children", []) or []:
            visit(c)

    visit(root)
    return id_match[0] or cls_match[0]


def count_nodes(node):
    """Total node count in the structure.json tree — the 'substantial content'
    signal for the degenerate-sections guard."""
    if not isinstance(node, dict):
        return 0
    n = 1
    for c in node.get("children", []) or []:
        n += count_nodes(c)
    return n


def normalize_section_id(s):
    sid = (s.get("id") or "").strip()
    if sid:
        return re.sub(r"[^a-z0-9_-]", "-", sid.lower())[:64]
    cls = (s.get("cls") or s.get("className") or "").strip()
    if cls:
        head = cls.split()[0]
        return re.sub(r"[^a-z0-9_-]", "-", head.lower())[:64]
    return f"section-{s.get('index', 0)}"


structure = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
styles_map = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
section_map_raw = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
out_path = Path(sys.argv[4])

sections = (
    section_map_raw.get("sections", [])
    if isinstance(section_map_raw, dict)
    else section_map_raw
)
if not isinstance(sections, list):
    sections = []

# Build the global tree first; downstream consumers (Phase 4 prompt) can
# either iterate sections[] or fall back to the global tree.
global_tree = walk(structure, styles_map)

# Per-section descriptors. structure.json is one big tree; we don't have a
# per-section subtree split. Provide section metadata + the agent looks up
# the matching part of the global tree by `top`/`height`/`class` keys.
sec_out = []
for s in sections:
    if not isinstance(s, dict):
        continue
    sid = normalize_section_id(s)
    cls = (s.get("cls") or s.get("className") or "").strip()
    # Aggregate (tag/class) is the gap-filler; the section's OWN root-node
    # computed styles win (RANK-3 fix). resolve_styles alone stamps the
    # page-modal padding/display on every section; per-node restores the real
    # per-section layout (its own padding/display/margin/min-height + RANK-1
    # fidelity props). Same per-node-wins precedence as walk().
    aggregate = resolve_styles(s.get("tag", "section"), cls, styles_map)
    sec_node = find_section_node(structure, s)
    per_node = shorten_node_styles(sec_node.get("styles") or {}) if sec_node else {}
    sec_styles = {**aggregate, **per_node}
    sec_out.append({
        "id": sid,
        "tag": s.get("tag", "section"),
        "class": cls,
        "top": s.get("top"),
        "height": s.get("height"),
        "styles": sec_styles,
    })

# Degenerate-sections guard: section-map.json is the authoritative section
# enumeration. A timing race (dom-scaffold run before extract-section-map.sh
# finished) yields a section-map with 0 usable sections even though
# structure.json already holds the full DOM. Emitting `sections: []` then is a
# SILENT degenerate scaffold — the generator freehands per-section layout
# (observed on 2 of 3 smoke clones: section-map later had 9/7 sections but the
# scaffold the generator consumed had 0). Mirror the text-fidelity
# degenerate-scaffold guard: fail loud, do NOT emit, demand re-extraction.
# Env escape hatch for genuinely section-less pages (preloader-only, etc.).
def _truthy(v):
    return str(v).strip().lower() not in ("", "0", "false", "no")


ALLOW_NO_SECTIONS = _truthy(os.environ.get("DOM_SCAFFOLD_ALLOW_NO_SECTIONS", ""))
NODE_FLOOR = int(os.environ.get("DOM_SCAFFOLD_NODE_FLOOR", "10"))
node_count = count_nodes(structure)
if not sec_out and node_count >= NODE_FLOOR and not ALLOW_NO_SECTIONS:
    sys.stderr.write(
        f"dom-scaffold: section-map.json has 0 usable sections but "
        f"structure.json has {node_count} nodes — section-map extraction is "
        "incomplete; re-run extract-section-map.sh before dom-scaffold "
        "(set DOM_SCAFFOLD_ALLOW_NO_SECTIONS=1 for genuinely section-less pages).\n"
    )
    sys.exit(3)

# Anti-fabrication rule string travels with the scaffold so the agent sees
# it the moment it reads the file.
RULE = (
    "Phase 4 generation rule (Fix 8 — DOM-structure-driven):\n"
    "1. The global tree below is the SOURCE OF TRUTH. Translate it to JSX\n"
    "   1:1 — same tag hierarchy, same nesting depth, same `text` verbatim.\n"
    "2. For every `styles` block, pick the closest-exact Tailwind utility:\n"
    "   bg: 'rgb(26,14,8)'   → 'bg-[#1a0e08]'\n"
    "   color: 'rgb(245,234,210)' → 'text-[#f5ead2]'\n"
    "   fs: '140px'          → 'text-[140px]'   (or 'text-9xl' if exact)\n"
    "   fw: '700'            → 'font-bold'\n"
    "   lh: '0.95'           → 'leading-[0.95]'\n"
    "   Measure-then-lookup. Do not 'feel' a class — compute it.\n"
    "3. If a tag's text is empty and no children are present, RENDER NOTHING\n"
    "   (do not invent placeholder text). text-fidelity-check.sh blocks\n"
    "   any JSX text-position string that is not in the scaffold tree.\n"
    "4. Group children into Section components by matching the sections[]\n"
    "   entries' (top, height, class) to subtrees in `tree`."
)

doc = {
    "_rule": RULE,
    "sections": sec_out,
    "tree": global_tree,
}

out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"dom-scaffold: wrote {out_path}")
print(f"  sections: {len(sec_out)}, global tree depth-capped to 8")
PY
