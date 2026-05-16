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
import re
import sys
from pathlib import Path


# Tailwind-relevant style keys to carry through. Drop the rest to keep the
# scaffold lean — Phase 4 doesn't need to look at every computed property.
STYLE_KEYS = (
    "display", "position", "bg", "color", "ff", "fs", "fw", "lh", "ls",
    "padding", "margin", "width", "height",
)


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
    styles/children. Caps depth so massive trees don't blow up the prompt.
    """
    if not isinstance(node, dict) or depth > max_depth:
        return None
    tag = node.get("tag", "")
    text = node.get("text", "") or ""
    cls = (node.get("class") or "")[:80]
    styles = resolve_styles(tag, cls, styles_map)
    children = []
    for c in node.get("children", []) or []:
        sub = walk(c, styles_map, depth + 1, max_depth)
        if sub is not None:
            children.append(sub)
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
    sec_styles = resolve_styles(s.get("tag", "section"), cls, styles_map)
    sec_out.append({
        "id": sid,
        "tag": s.get("tag", "section"),
        "class": cls,
        "top": s.get("top"),
        "height": s.get("height"),
        "styles": sec_styles,
    })

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
