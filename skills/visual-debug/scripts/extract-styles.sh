#!/usr/bin/env bash
# extract-styles.sh — aggregate structure.json per-node styles into styles.json.
#
# Closes the Phase 2 contract gap that dom-scaffold.sh has assumed forever:
# the scaffold consumes structure.json + styles.json + section-map.json, but
# only structure.json was produced by extract-dom.sh. styles.json was not
# documented anywhere — agents had to invent it or the scaffold simply
# aborted on fresh-only runs.
#
# extract-dom.sh already captures per-node computed styles in structure.json
# (under each node's `styles` key). This script walks that file, groups the
# values by HTML tag and by first class token, and emits the per-tag-or-class
# aggregate dom-scaffold.sh's `resolve_styles()` expects:
#
#   {
#     "<tag>":   {"display": "...", "color": "...", "ff": "...", ...},
#     ".<cls>":  {"bg": "...", "fs": "...", ...},
#     ...
#   }
#
# Key shortening (CSS property -> shorthand) matches the keys dom-scaffold's
# STYLE_KEYS list checks: bg, color, ff, fs, fw, lh, ls, display, position,
# padding, margin, width, height. background-image wins over background-color
# when both are present so gradient/asset backgrounds survive into the
# scaffold.
#
# Aggregation rule: for each (tag-or-class, shortkey) pair, take the most
# frequent non-empty value across all matching nodes. Ties broken by first
# occurrence. This converges to "the typical style for this tag/class on the
# page" — the same logic an agent reading a dom-extraction.md walkthrough
# would apply by hand.
#
# Usage:
#   extract-styles.sh <ref-dir>

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: extract-styles.sh <ref-dir>" >&2
  exit 2
fi

REF_DIR="$1"
if [ ! -d "$REF_DIR" ]; then
  echo "extract-styles: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
STRUCT="$REF_DIR/structure.json"
if [ ! -f "$STRUCT" ]; then
  echo "extract-styles: structure.json missing: run extract-dom.sh first" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/styles.json"

python3 - "$STRUCT" "$OUT_PATH" <<'PY'
import json
import sys
from collections import Counter, defaultdict

src, dst = sys.argv[1], sys.argv[2]

# CSS property -> shorthand key dom-scaffold's STYLE_KEYS understands.
# background-image is checked before background-color so gradients / asset
# backgrounds beat the fallback solid color when both are present on a node.
SHORTHAND = (
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
    # Fidelity-critical props extract-dom.sh already captures into
    # structure.json but were dropped here — so the generator (which reads
    # dom-scaffold.json) never saw a real shadow / radius / alignment and
    # freehanded all of them. Keyed by full CSS name (no shorthand) so the
    # value reaches Phase 4 self-documenting. ADDITIVE — no existing key
    # removed or weakened.
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

NOISE = {"", "normal", "none", "auto", "0px", "rgba(0, 0, 0, 0)"}


def shorten_styles(raw):
    """Translate a per-node computed-style dict (full CSS prop names) into
    the shorthand keyspace dom-scaffold consumes. Empty/noise values dropped.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    bg_image = raw.get("background-image")
    bg_color = raw.get("background-color")
    bg = None
    if isinstance(bg_image, str) and bg_image.strip() and bg_image.strip() not in NOISE:
        bg = bg_image.strip()
    elif isinstance(bg_color, str) and bg_color.strip() and bg_color.strip() not in NOISE:
        bg = bg_color.strip()
    if bg:
        out["bg"] = bg
    for css_name, short in SHORTHAND:
        v = raw.get(css_name)
        if isinstance(v, str):
            vv = v.strip()
            if vv and vv not in NOISE:
                out[short] = vv
    return out


# Per (key, shortkey) collect value frequencies. Take the modal value at the
# end. defaultdict(Counter) makes the inner accumulator implicit.
buckets: defaultdict = defaultdict(lambda: defaultdict(Counter))


# Class-level aggregation is safe for properties that design systems hold
# stable across all instances of a class — typography, color, background.
# Structural properties (width, height, padding, margin, display, position)
# vary instance-by-instance even within a single class: a `.card` instance
# inside a hero is 800px wide while the catalog grid `.card` is 320px.
# Modal aggregation of those keys at the class level silently stamps the
# dominant size onto exceptional instances, which Phase 4 then reproduces
# as the wrong layout. dom-scaffold's walk() consumes per-node `styles`
# from structure.json directly for these, so the class aggregate is the
# *fallback* — not the source of truth — and dropping structural keys here
# keeps it from poisoning the fallback path.
#
# Review follow-up 2026-05-22 (Q1): without this carve-out the modal aggregate
# is the only source of structural styles a Phase-4 consumer ever sees,
# and exceptional instances inherit the dominant class's layout.
CLASS_LEVEL_STRUCTURAL_KEYS = {
    "display", "position", "padding", "margin", "width", "height",
    # The fidelity props added to SHORTHAND that ALSO vary instance-by-instance
    # (offsets, sizing bounds, transforms, grid tracks, gaps, z-order) get the
    # same carve-out: aggregating them at the class level would stamp the
    # dominant instance's value onto exceptions, and dom-scaffold reads these
    # per-node from structure.json directly (per-node wins). Design-system-stable
    # fidelity props (border-radius/border/box-shadow/text-align/text-transform/
    # white-space/overflow/opacity/justify-content/align-items/flex-direction)
    # stay at class level — they're consistent across instances of a class.
    "z-index", "top", "left", "right", "bottom", "transform",
    "grid-template-columns", "grid-template-rows", "gap", "flex",
    "min-width", "max-width", "min-height", "max-height",
}


def walk(node):
    if not isinstance(node, dict):
        return
    tag_raw = node.get("tag")
    tag = tag_raw.lower() if isinstance(tag_raw, str) else None
    styles = shorten_styles(node.get("styles") or {})
    cls_raw = node.get("class") or node.get("className") or ""
    first_cls = ""
    if isinstance(cls_raw, str) and cls_raw.strip():
        first_cls = cls_raw.strip().split()[0]
    # Per-tag bucket gets everything; per-first-class bucket skips structural.
    if tag:
        for shortk, val in styles.items():
            buckets[tag][shortk][val] += 1
    if first_cls:
        cls_key = f".{first_cls}"
        for shortk, val in styles.items():
            if shortk in CLASS_LEVEL_STRUCTURAL_KEYS:
                continue
            buckets[cls_key][shortk][val] += 1
    for child in node.get("children", []) or []:
        walk(child)


with open(src, encoding="utf-8") as f:
    structure = json.load(f)
# Some upstreams double-encode; unwrap once.
if isinstance(structure, str):
    structure = json.loads(structure)

walk(structure)

# Settle each (key, shortkey) to its modal value (ties: first occurrence).
result: dict = {}
for key, shortmap in buckets.items():
    settled = {}
    for shortk, counter in shortmap.items():
        if not counter:
            continue
        top_val, _ = counter.most_common(1)[0]
        settled[shortk] = top_val
    if settled:
        result[key] = settled

with open(dst, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(f"extract-styles: wrote {dst}")
print(f"  keys: {len(result)} (tags + first-class buckets)")
PY
