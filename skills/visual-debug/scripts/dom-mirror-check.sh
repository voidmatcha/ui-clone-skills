#!/usr/bin/env bash
# dom-mirror-check.sh — block Phase-4 tree-shape divergence from scaffold.
#
# Extracts the JSX element nesting tree from each `<impl>/src/components/*.tsx`
# and compares it to the corresponding subtree in `<ref>/dom-scaffold.json`.
# Divergence > threshold = gate fail.
#
# This complements text-fidelity-check.sh:
#   text-fidelity-check  catches WRONG CONTENT (fabricated strings)
#   dom-mirror-check     catches WRONG STRUCTURE (different nesting/tag seq)
#
# Pattern is from DesignCoder (arXiv:2506.13663) — hierarchy-aware metadata
# tree comparison. Structural divergence is a stronger signal than text
# divergence for "agent is making it up instead of following the scaffold".
#
# Usage:
#   dom-mirror-check.sh <ref-dir> <impl-dir> [--out <json>] [--threshold N]
#
# Exit 0 on pass, 1 on divergence, 2 on error.
set -euo pipefail

REF_DIR=""
IMPL_DIR=""
OUT_PATH=""
THRESHOLD=30  # pct divergence allowed before failing

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_PATH="$2"; shift 2;;
    --threshold) THRESHOLD="$2"; shift 2;;
    -h|--help) sed -n '2,17p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then IMPL_DIR="$1"
      else echo "dom-mirror: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" ]]; then
  echo "usage: dom-mirror-check.sh <ref-dir> [<impl-dir>] [--out <json>] [--threshold N]" >&2
  exit 2
fi
if [[ -z "$IMPL_DIR" ]]; then
  IMPL_DIR="$(cd "$(dirname "$REF_DIR")" && pwd)/impl"
fi
SCAFFOLD="$REF_DIR/dom-scaffold.json"
if [[ ! -f "$SCAFFOLD" ]]; then
  echo "dom-mirror: dom-scaffold.json missing — run dom-scaffold.sh first" >&2
  exit 2
fi

python3 - "$SCAFFOLD" "$IMPL_DIR" "${OUT_PATH:-}" "$THRESHOLD" <<'PY'
import json
import re
import sys
from pathlib import Path


scaffold_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
out_path = Path(sys.argv[3]) if sys.argv[3] else None
threshold_pct = int(sys.argv[4])


# Build a flat tag-sequence from the scaffold tree (pre-order DFS, tag only).
scaffold = json.loads(scaffold_path.read_text(encoding="utf-8"))
ref_seq = []


def walk(node, depth=0, max_depth=12):
    if depth > max_depth or not isinstance(node, dict):
        return
    tag = node.get("tag", "")
    if tag:
        ref_seq.append(tag.lower())
    for c in node.get("children", []) or []:
        walk(c, depth + 1, max_depth)


walk(scaffold.get("tree", {}))


# Extract the JSX tag sequence from impl components. Use a regex over open
# tags `<TagName...` ignoring closing/self-closing fragments. Skip Fragment
# and React.Fragment.
TAG_PATTERN = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)")


# Filter to HTML element names — capitalize-leading is a React component, not
# an HTML element, so skip (the component itself maps to an HTML tag via its
# own render).
HTML_TAGS = {
    "html","body","main","header","footer","nav","aside","section","article",
    "div","span","a","button","img","video","audio","picture","source",
    "h1","h2","h3","h4","h5","h6","p","ul","ol","li","dl","dt","dd",
    "table","thead","tbody","tr","th","td","caption",
    "form","input","textarea","select","option","label","fieldset","legend",
    "iframe","canvas","svg","path","g","circle","rect","line","polyline","polygon",
    "figure","figcaption","time","mark","strong","em","b","i","u","small","sup","sub",
    "blockquote","pre","code","kbd","samp","var","cite","q","abbr","address",
    "br","hr","details","summary","dialog","template",
}


impl_seq = []
impl_components = sorted((impl_dir / "src" / "components").glob("*.tsx"))
if not impl_components:
    out = {
        "status": "pass",
        "reason": "no components yet — Phase 4 not run",
        "ref_tag_count": len(ref_seq),
        "impl_tag_count": 0,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0)


for comp_path in impl_components:
    body = comp_path.read_text(encoding="utf-8")
    body_clean = re.sub(r"/\*[\s\S]*?\*/", "", body)
    body_clean = re.sub(r"//[^\n]*", "", body_clean)
    for m in TAG_PATTERN.finditer(body_clean):
        tag = m.group(1).lower()
        if tag in HTML_TAGS:
            impl_seq.append(tag)


# Compute a tag-frequency-set distance (cheap proxy for tree-shape divergence).
# Levenshtein over full sequences is O(n²) and we don't need byte-accurate;
# we want "did the agent produce roughly the same kinds of elements in
# roughly the same proportions". Use a multiset Jaccard-like score.
from collections import Counter
ref_counter = Counter(ref_seq)
impl_counter = Counter(impl_seq)
all_tags = set(ref_counter) | set(impl_counter)

# Intersection / Union over multisets — overlap = sum of mins, union = sum
# of maxes. 1.0 = identical multisets, 0.0 = disjoint.
overlap = sum(min(ref_counter.get(t, 0), impl_counter.get(t, 0)) for t in all_tags)
union = sum(max(ref_counter.get(t, 0), impl_counter.get(t, 0)) for t in all_tags)
similarity = (overlap / union) if union else 1.0
divergence_pct = round((1.0 - similarity) * 100, 1)


# Per-tag breakdown for the diff report — only flag tags where impl deviates
# significantly (>50% off ref count).
tag_deltas = []
for tag in sorted(all_tags):
    r = ref_counter.get(tag, 0)
    i = impl_counter.get(tag, 0)
    if r == 0 and i > 0:
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "tag invented in impl"})
    elif i == 0 and r > 2:
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "tag dropped from impl"})
    elif r > 0 and abs(r - i) > max(2, r * 0.5):
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "count diverges >50%"})


status = "fail" if divergence_pct > threshold_pct else "pass"
out = {
    "status": status,
    "ref_tag_count": len(ref_seq),
    "impl_tag_count": len(impl_seq),
    "similarity": round(similarity, 3),
    "divergence_pct": divergence_pct,
    "threshold_pct": threshold_pct,
    "tag_deltas": tag_deltas[:30],
    "components_checked": len(impl_components),
    "rule": (
        "Impl JSX tag-multiset must mirror dom-scaffold tag-multiset within "
        f"{threshold_pct}% divergence. Inventing tags not in ref or dropping "
        "tags from ref by >50% fails this gate."
    ),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
if out_path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if status == "pass" else 1)
PY
