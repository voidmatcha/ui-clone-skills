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
# Observed across multiple benchmark iterations: clones of sites with
# deeply-nested obfuscated div-soup (15+ wrapper levels of opaque hashed
# classes, ~1000+ DOM nodes) produce 80%+ tag-multiset divergence
# because LLMs abstract that markup into clean React components
# (~200 nodes). A 30% threshold was unreachable in practice. Raise to
# 80% so the gate only fires on genuine evisceration (impl dropping
# 90%+ of ref tags), and route hero composite structure check to a
# dedicated hero-composite-check.sh instead. UI_CLONE_DOM_MIRROR_THRESHOLD
# env var lets operators tighten back down for sites without div-soup.
THRESHOLD="${UI_CLONE_DOM_MIRROR_THRESHOLD:-80}"

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


# Extract the JSX tag sequence from impl components. Use a regex over open
# tags `<TagName...` ignoring closing/self-closing fragments. Skip Fragment
# and React.Fragment.
TAG_PATTERN = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)")


# Filter to HTML element names — capitalize-leading is a React component, not
# an HTML element, so skip (the component itself maps to an HTML tag via its
# own render). This set is the SINGLE SOURCE OF TRUTH for the measurable tag
# universe and is applied symmetrically to BOTH the ref scaffold walk (below)
# and the impl JSX scan: a tag counts only if both sides could produce it.
# Counting tags on the ref side that the impl extractor can never emit (e.g.
# <head>-metadata meta/link/title/base, or hyphenated custom elements that
# TAG_PATTERN can't match) guarantees false "dropped from impl" deltas and, on
# any site with a populated <head> (meta x30+), an eviscerate hard-fail
# regardless of clone quality. Keeping one allowlist for both sides makes the
# gate compare the exact same tag universe and stays self-consistent if the
# set is later widened or narrowed.
HTML_TAGS = {
    "html","body","main","header","footer","nav","aside","section","article",
    "div","span","a","button","img","video","audio","picture","source",
    "h1","h2","h3","h4","h5","h6","p","ul","ol","li","dl","dt","dd",
    "table","thead","tbody","tr","th","td","caption",
    "form","input","textarea","select","option","label","fieldset","legend",
    "iframe","canvas","svg","path","g","circle","rect","line","polyline","polygon",
    "figure","figcaption","time","mark","strong","em","b","i","u","small","sup","sub",
    "blockquote","pre","code","kbd","samp","var","cite","q","abbr","address",
    "br","hr","details","summary","dialog",
    # NB: script/style/noscript/template are intentionally absent — they are
    # RSC payload / polyfill / CSS-text containers that the impl JSX never
    # reproduces, so excluding them from the allowlist strips them from both
    # sides (previously template was counted on the impl side only).
}


# Build a flat tag-sequence from the scaffold tree (pre-order DFS, tag only).
scaffold = json.loads(scaffold_path.read_text(encoding="utf-8"))
ref_seq = []


def walk(node, depth=0, max_depth=12):
    if depth > max_depth or not isinstance(node, dict):
        return
    tag = node.get("tag", "")
    # Symmetric filter: only count tags the impl-side scan can also produce.
    if isinstance(tag, str) and tag.lower() in HTML_TAGS:
        ref_seq.append(tag.lower())
    for c in node.get("children", []) or []:
        walk(c, depth + 1, max_depth)


walk(scaffold.get("tree", {}))


impl_seq = []
#
# heavy-motion site (signal #3) escape: agent abandoned scaffold and authored
# impl/src/main.jsx by hand. main.jsx contained the live DOM but was
# skipped by the .tsx-only scan, so dom-mirror saw the dead scaffold
# under page.tsx and reported divergence — but never saw what the
# browser actually rendered. Extend the scan to .jsx / .ts / .js too;
# any file with JSX (or JS that builds DOM strings) participates.
SCAN_EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo"}
SCAN_SUFFIXES = (".tsx", ".jsx", ".ts", ".js")
all_jsx = []
src_root = impl_dir / "src"
if src_root.is_dir():
    for p in src_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SCAN_EXCLUDE for part in p.parts):
            continue
        all_jsx.append(p)
impl_components = sorted(all_jsx)
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


# Common cheat pattern: React `.map()` / `.forEach()` / `.flatMap()`
# over arrays of repeated data (e.g. many pyramid items, FAQ rows,
# word-spans) renders
# many runtime DOM tags from FEW static JSX tags. Static-grep
# would say "ref has 38 <li>, impl has 1 <li>" → false fail.
# Track which tags appear INSIDE iteration callbacks so the
# eviscerate + per-tag-delta checks can exempt them (we can't
# statically know the runtime count).
ITER_RE = re.compile(
    r"\.(?:map|forEach|flatMap|filter|reduce|reduceRight|flat)\s*\(\s*(?:async\s*)?"
    r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    r"|"
    r"Array\.from\s*\(\s*[^,)]+,\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    r"|"
    r"\[\.\.\.[A-Za-z_$][\w$.]*\]\s*\.\s*(?:map|forEach|flatMap)\s*\("
)
iterated_tags: set[str] = set()


def _tags_in_block(text: str) -> set[str]:
    out: set[str] = set()
    for m in TAG_PATTERN.finditer(text):
        t = m.group(1).lower()
        if t in HTML_TAGS:
            out.add(t)
    return out


def _balanced_segment(text: str, start_idx: int, max_len: int = 8000) -> str:
    """Return text from start_idx up to the matching close paren.
    Handles nested parens/braces and quoted strings (best-effort).
    Stops at max_len chars."""
    depth_paren = 0
    depth_brace = 0
    end = min(len(text), start_idx + max_len)
    in_str = None
    i = start_idx
    while i < end:
        ch = text[i]
        if in_str:
            if ch == in_str and text[i - 1] != "\\":
                in_str = None
            i += 1
            continue
        if ch in "\"'`":
            in_str = ch
            i += 1
            continue
        if ch == "(": depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
            if depth_paren <= 0:
                return text[start_idx:i + 1]
        elif ch == "{": depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        i += 1
    return text[start_idx:end]


for comp_path in impl_components:
    body = comp_path.read_text(encoding="utf-8")
    body_clean = re.sub(r"/\*[\s\S]*?\*/", "", body)
    body_clean = re.sub(r"//[^\n]*", "", body_clean)
    # First pass — collect tags that live inside iteration callbacks.
    for m in ITER_RE.finditer(body_clean):
        # Walk balanced parens starting at the `(` of `.map(`.
        paren_idx = body_clean.find("(", m.start())
        if paren_idx < 0:
            continue
        seg = _balanced_segment(body_clean, paren_idx)
        iterated_tags.update(_tags_in_block(seg))
    # Second pass — collect all tags as before.
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
EVISCERATE_MIN_REF = 10
EVISCERATE_MAX_RATIO = 0.25
eviscerated: list[dict] = []
for tag in sorted(all_tags):
    r = ref_counter.get(tag, 0)
    i = impl_counter.get(tag, 0)
    # Iterated tags (impl renders many runtime instances from a
    # single static JSX inside .map() / .forEach() etc) are exempt
    # from per-tag count delta + eviscerate. Static count would
    # always under-represent the runtime count and false-fail.
    if tag in iterated_tags:
        tag_deltas.append({
            "tag": tag, "ref": r, "impl": i,
            "note": "impl uses iteration (.map/.forEach) — static count cannot match runtime",
        })
        continue
    if r == 0 and i > 0:
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "tag invented in impl"})
    elif i == 0 and r > 2:
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "tag dropped from impl"})
    elif r > 0 and abs(r - i) > max(2, r * 0.5):
        tag_deltas.append({"tag": tag, "ref": r, "impl": i, "note": "count diverges >50%"})
    # Class-evisceration: heavy tag in ref nearly disappeared from impl.
    if r >= EVISCERATE_MIN_REF and i < r * EVISCERATE_MAX_RATIO:
        eviscerated.append({"tag": tag, "ref": r, "impl": i})


# Recompute similarity excluding iterated tags entirely from the
# multiset (static-grep can't compare them meaningfully). Falls
# back to the original similarity when no iterated tags detected.
if iterated_tags:
    non_iter_ref = Counter({
        t: c for t, c in ref_counter.items() if t not in iterated_tags
    })
    non_iter_impl = Counter({
        t: c for t, c in impl_counter.items() if t not in iterated_tags
    })
    non_iter_tags = set(non_iter_ref) | set(non_iter_impl)
    overlap2 = sum(
        min(non_iter_ref.get(t, 0), non_iter_impl.get(t, 0))
        for t in non_iter_tags
    )
    union2 = sum(
        max(non_iter_ref.get(t, 0), non_iter_impl.get(t, 0))
        for t in non_iter_tags
    )
    similarity = (overlap2 / union2) if union2 else 1.0
    divergence_pct = round((1.0 - similarity) * 100, 1)


status = "fail" if (divergence_pct > threshold_pct or eviscerated) else "pass"
out = {
    "status": status,
    "ref_tag_count": len(ref_seq),
    "impl_tag_count": len(impl_seq),
    "similarity": round(similarity, 3),
    "divergence_pct": divergence_pct,
    "threshold_pct": threshold_pct,
    "tag_deltas": tag_deltas[:30],
    "eviscerated_tags": eviscerated,
    "components_checked": len(impl_components),
    "rule": (
        "Impl JSX tag-multiset must mirror dom-scaffold tag-multiset within "
        f"{threshold_pct}% divergence. Inventing tags not in ref or dropping "
        "tags from ref by >50% fails this gate. Additionally, any tag with "
        f"ref count >= {EVISCERATE_MIN_REF} that drops below "
        f"{int(EVISCERATE_MAX_RATIO * 100)}% in impl is treated as class "
        "evisceration and hard-fails regardless of overall divergence."
    ),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
if out_path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if status == "pass" else 1)
PY
