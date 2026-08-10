#!/usr/bin/env bash
# hero-composite-check.sh — gate the most common "looks similar but
# elements don't match" failure mode observed in clone iterations.
#
# Symptom pattern: ref's hero section is a 4-layer composite
#   <section class="hero">
#     <video .../>                       ← background media
#     <button class="hero-video">         ← overlay click target
#       <video .../>                      (sometimes nested again)
#       <span class="hero-video__label">  ← accessible label
#     </button>
#     <h1>...</h1>                        ← headline
#   </section>
# LLM-generated clones often flatten this into a clean React tree
# (e.g. `<HeroSection><video/><h1>...</h1></HeroSection>`) — dropping the
# overlay button and the label span. tree-diff catches it via unpaired
# BUTTON/VIDEO/SPAN/H1 elements, but tree-diff is too noisy on the broader
# structural divergence (downgraded to advisory in 2026-05-22 retune).
# This check is the spot-specific replacement: parse ref's hero subtree,
# inventory which of the 4 element kinds are present, and verify impl
# has all of them in the corresponding hero region.
#
# What counts as "the impl hero region", in priority order:
#   1. File containing `data-section="hero"` (or data-section='hero')
#      — explicit locator, strongest signal. Matches the find-impl-root
#      preference for explicit markers over heuristics.
#   2. File path or content containing "Hero" / "hero" (case-insensitive).
#   3. Any file containing `<video` somewhere in its top-level JSX.
# If multiple match, evaluate the union (any one having all 4 kinds = PASS).
#
# Element-kind detection on impl side:
#   videoTag   : matches `<video` anywhere in the candidate file(s)
#   buttonTag  : matches `<button` AND has a `<video` within 500 chars
#                (forward or back) — proximity check excludes navbar
#                buttons that just happen to be in the same file
#   headline   : matches `<h1` OR `<h2`
#   labelSpan  : matches `<span` anywhere
# Soft on purpose: we are not checking nesting depth, just presence with
# proximity. The LLM consistently drops these elements entirely, not
# misplaces them — so presence is the right primary signal. The button
# proximity adds the one tightening that excludes false-positive
# co-presence with unrelated navbar/footer buttons.
#
# Ref-side detection: read structure.json for the page tree, find the
# section whose className/id matches /hero/i, walk its subtree looking
# for the 4 element kinds.
#
# Output: <ref-dir>/hero-composite.json
#   {
#     "schemaVersion": 1,
#     "status": "pass" | "fail" | "warn",
#     "ref": {"video": true, "button": true, "h1OrH2": true, "label": true},
#     "impl": {"video": true, "button": false, "h1OrH2": true, "label": false},
#     "missingInImpl": ["button", "label"],
#     "implCandidateFiles": ["src/components/HeroSection.tsx", ...],
#     "rule": "Impl hero region must contain every element kind present in ref hero (video, button, h1/h2, label span). Missing kinds drop the gate to fail."
#   }
#
# Usage:
#   hero-composite-check.sh <ref-dir> <impl-dir> [--out <json>]
#
# Exit 0 on pass/warn, 1 on fail, 2 on setup error.

set -uo pipefail

REF_DIR=""
IMPL_DIR=""
OUT_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_PATH="$2"; shift 2;;
    -h|--help) sed -n '2,60p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then IMPL_DIR="$1"
      else echo "hero-composite: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" ]]; then
  echo "usage: hero-composite-check.sh <ref-dir> [<impl-dir>] [--out <json>]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "hero-composite: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

# Resolve impl dir via the canonical resolver if not passed.
if [[ -z "$IMPL_DIR" ]]; then
  PLUGIN_R="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}}}}"
  if [[ -x "$PLUGIN_R/scripts/extract/find-impl-root.sh" ]]; then
    IMPL_DIR=$(bash "$PLUGIN_R/scripts/extract/find-impl-root.sh" "$REF_DIR" 2>/dev/null | head -1)
  fi
fi
if [[ -z "$IMPL_DIR" || ! -d "$IMPL_DIR" ]]; then
  echo "hero-composite: impl-dir not found (got: ${IMPL_DIR:-empty})" >&2
  exit 2
fi

OUT_PATH="${OUT_PATH:-$REF_DIR/hero-composite.json}"

python3 - "$REF_DIR" "$IMPL_DIR" "$OUT_PATH" <<'PY'
# Python 3.9 compat: union syntax via future-import.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
out_path = Path(sys.argv[3])


def write_artifact(payload: dict) -> None:
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


# ── Ref side: walk structure.json, find hero section, inventory kinds.
struct_path = ref_dir / "structure.json"
if not struct_path.is_file():
    write_artifact({
        "schemaVersion": 1,
        "status": "warn",
        "note": "structure.json absent — skipped",
        "rule": "Hero composite check requires structure.json from Phase 2 extract-dom.sh.",
    })
    print("hero-composite: SKIP (no structure.json)")
    sys.exit(0)

try:
    structure = json.loads(struct_path.read_text(encoding="utf-8"))
    if isinstance(structure, str):
        structure = json.loads(structure)
except (json.JSONDecodeError, OSError) as exc:
    write_artifact({
        "schemaVersion": 1,
        "status": "warn",
        "note": f"structure.json malformed: {exc}",
        "rule": "Hero composite check requires a parseable structure.json.",
    })
    print(f"hero-composite: SKIP (malformed structure.json: {exc})")
    sys.exit(0)


HERO_CLASS_RE = re.compile(
    r"hero|banner|masthead|intro|fold-?1|first-?view|\bkv\b|key[-_]?visual|page-?top",
    re.IGNORECASE,
)
STRONG_HERO_RE = re.compile(
    r"hero|masthead|fold-?1|first-?view|\bkv\b|key[-_]?visual|page-?top",
    re.IGNORECASE,
)


def collect_hero_subtrees(node, hits=None):
    """Collect EVERY node whose class/id contains 'hero'. Critical:
    ref hero composites often split the visible section and its
    background-video container into SIBLING elements (e.g.
    `<section class="prefix_hero__X">` next to
    `<div class="prefix_hero_video__Y">` — the video is NOT a descendant
    of the section). A single-node hero-finder misses the video. We
    collect every hero-named subtree and treat the union as the
    composite for kind-presence checks.
    """
    if hits is None:
        hits = []
    if not isinstance(node, dict):
        return hits
    tag = (node.get("tag") or "").lower()
    cls = str(node.get("class") or node.get("className") or "")
    node_id = str(node.get("id") or "")
    if tag in {"section", "header", "main", "div", "article"} and (
        HERO_CLASS_RE.search(cls) or HERO_CLASS_RE.search(node_id)
    ):
        hits.append(node)
        # Don't recurse into a hero match — its descendants are already
        # part of this subtree. (Re-collecting them as separate hero
        # nodes would inflate the kind count without changing semantics.)
        return hits
    for child in node.get("children", []) or []:
        collect_hero_subtrees(child, hits)
    return hits


def fallback_top_section(node):
    """When no hero-named container found, use the first top-level section."""
    if not isinstance(node, dict):
        return None
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            tag = (child.get("tag") or "").lower()
            if tag in {"section", "header", "main"}:
                return child
    return None


hero_subtrees = collect_hero_subtrees(structure)
strong_hero_subtrees = [
    node for node in hero_subtrees
    if STRONG_HERO_RE.search(
        f"{node.get('class') or node.get('className') or ''} {node.get('id') or ''}"
    )
]
if strong_hero_subtrees:
    # Generic banners (cookie notices, flash alerts, promo strips) frequently
    # contain buttons and otherwise pollute a real hero's element inventory.
    # Once a strong hero/key-visual match exists, broad "banner"/"intro"
    # fallbacks must not be unioned into the same composite.
    hero_subtrees = strong_hero_subtrees
if not hero_subtrees:
    write_artifact({
        "schemaVersion": 1,
        "status": "skip",
        "note": "no hero-named class/id detected in ref — gate does not apply to this site",
        "rule": "Hero composite check requires a class/id containing 'hero' in structure.json. Skipped for sites without a hero pattern (dashboards, blogs, e-commerce grids).",
    })
    print("hero-composite: SKIP (no hero section in ref)")
    sys.exit(0)


def has_kind(node, predicate) -> bool:
    """Returns True when any descendant matches the predicate."""
    if not isinstance(node, dict):
        return False
    if predicate(node):
        return True
    for child in node.get("children", []) or []:
        if has_kind(child, predicate):
            return True
    return False


def is_tag(name: str):
    def pred(node) -> bool:
        return isinstance(node, dict) and (node.get("tag") or "").lower() == name
    return pred


def is_headline(node) -> bool:
    if not isinstance(node, dict):
        return False
    tag = (node.get("tag") or "").lower()
    return tag in {"h1", "h2"}


def any_subtree_has(predicate) -> bool:
    return any(has_kind(t, predicate) for t in hero_subtrees)


ref_kinds = {
    "video": any_subtree_has(is_tag("video")),
    "button": any_subtree_has(is_tag("button")),
    "h1OrH2": any_subtree_has(is_headline),
    "label": any_subtree_has(is_tag("span")),
    "canvas": any_subtree_has(is_tag("canvas")),
}


# ── Impl side: scan src/**/*.tsx (and .jsx) for hero-named files,
# inventory the same kinds via simple regex presence checks.
src_root = impl_dir / "src"
if not src_root.is_dir():
    # Fallback to impl root itself (some projects don't use src/).
    src_root = impl_dir

ts_files = []
for ext in ("*.tsx", "*.jsx", "*.ts", "*.js"):
    ts_files.extend(src_root.rglob(ext))

HERO_FILE_RE = re.compile(r"hero", re.IGNORECASE)
DATA_SECTION_HERO_RE = re.compile(
    r"""data-section\s*=\s*[\"']hero[\"']""", re.IGNORECASE
)
HERO_REGION_MARKER_RE = re.compile(
    r"""(?:data-testid\s*=\s*[\"']hero[\"']|"""
    r"""id\s*=\s*[\"']landing[\"']|"""
    # Plain class token (`class="hero"`, `class="page hero-inner"`) OR a
    # CSS-module hashed token (`dga-module__LrmiHG__hero`,
    # `mod__hero_video`) — the module form has no word boundary before
    # `hero`, so `\bhero\b` alone skips the real hero section file.
    r"""class(?:Name)?\s*=\s*[\"'][^\"']*(?:\bhero\b|[-_]hero(?![A-Za-z0-9])))""",
    re.IGNORECASE,
)

# Priority 1: explicit `data-section="hero"` marker (strongest signal).
# Priority 1 also accepts grounded semantic hero-region markers emitted by
# common design systems (`data-testid="Hero"`, `id="landing"`, hero class).
# Priority 2: file path/name contains "hero" (case-insensitive).
# Priority 3: file contains `<video` anywhere (catches Banner/Cover/Splash).
def file_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


candidates_p1 = []
candidates_p2 = []
candidates_p3 = []
for p in ts_files:
    if "node_modules" in p.parts:
        continue
    text = file_text(p)
    if DATA_SECTION_HERO_RE.search(text) or HERO_REGION_MARKER_RE.search(text):
        candidates_p1.append(p)
        continue
    if HERO_FILE_RE.search(str(p.relative_to(impl_dir))):
        candidates_p2.append(p)
        continue
    if "<video" in text.lower():
        candidates_p3.append(p)

# Use the highest-priority set that produced any candidates.
hero_files = candidates_p1 or candidates_p2 or candidates_p3

if not hero_files:
    write_artifact({
        "schemaVersion": 1,
        "status": "fail",
        "ref": ref_kinds,
        "impl": {"video": False, "button": False, "h1OrH2": False, "label": False, "canvas": False},
        "missingInImpl": ["hero-component-file"],
        "implCandidateFiles": [],
        "rule": (
            "Impl must have at least one hero-named component, a grounded "
            "hero-region marker, or a component containing <video>. None "
            "found in impl/src/."
        ),
    })
    print("hero-composite: FAIL (no hero candidate file in impl)")
    sys.exit(1)


# Inventory element kinds across the union of hero candidate files.
# When the reference hero contains video, button gets a proximity check:
# a `<button` only counts when there's also a `<video` within 500
# characters (forward or back). That excludes the false-positive where a
# navbar / footer button happens to live in the same file as the hero
# video. Non-video hero regions still count their real button candidates.
VIDEO_RE = re.compile(r"<\s*video\b", re.IGNORECASE)
BUTTON_RE = re.compile(r"<\s*button\b", re.IGNORECASE)
HEADLINE_RE = re.compile(r"<\s*h[12]\b", re.IGNORECASE)
SPAN_RE = re.compile(r"<\s*span\b", re.IGNORECASE)
CANVAS_RE = re.compile(r"<\s*canvas\b", re.IGNORECASE)
BUTTON_VIDEO_PROXIMITY = 500  # chars

# Transpiler-generated components (scaffold-to-jsx) carry 300-900 char
# inline `style={{ ... }}` objects on EVERY element, so a structurally
# adjacent overlay button lands thousands of raw characters from the hero
# video. Collapse style/class payloads before measuring distance: the rule
# is meant to express structural proximity, not attribute verbosity.
STYLE_ATTR_RE = re.compile(r"style\s*=\s*\{\{.*?\}\}", re.DOTALL)
CLASS_ATTR_RE = re.compile(
    r"""class(?:Name)?\s*=\s*[\"'][^\"']*[\"']""", re.IGNORECASE
)


def normalize_for_proximity(text: str) -> str:
    """Strip inline-style and class payloads so proximity measures structure."""
    text = STYLE_ATTR_RE.sub("style={{}}", text)
    return CLASS_ATTR_RE.sub('className=""', text)


def has_button_near_video(text: str) -> bool:
    """True when a `<button` occurrence has a `<video` within
    BUTTON_VIDEO_PROXIMITY characters in either direction, measured on
    attribute-normalized text.
    """
    text = normalize_for_proximity(text)
    video_positions = [m.start() for m in VIDEO_RE.finditer(text)]
    if not video_positions:
        return False
    for m in BUTTON_RE.finditer(text):
        bpos = m.start()
        for vpos in video_positions:
            if abs(bpos - vpos) <= BUTTON_VIDEO_PROXIMITY:
                return True
    return False


def has_relevant_button(text: str) -> bool:
    """Apply video proximity only to video-backed reference heroes."""
    if ref_kinds["video"]:
        return has_button_near_video(text)
    return BUTTON_RE.search(text) is not None


impl_kinds = {"video": False, "button": False, "h1OrH2": False, "label": False, "canvas": False}
for p in hero_files:
    text = file_text(p)
    if VIDEO_RE.search(text):
        impl_kinds["video"] = True
    if has_relevant_button(text):
        impl_kinds["button"] = True
    if HEADLINE_RE.search(text):
        impl_kinds["h1OrH2"] = True
    if SPAN_RE.search(text):
        impl_kinds["label"] = True
    if CANVAS_RE.search(text):
        impl_kinds["canvas"] = True
    if all(impl_kinds.values()):
        break

missing = [k for k, v in ref_kinds.items() if v and not impl_kinds[k]]

# Canvas-replay coherence: when canvas-replay-plan.json declares this hero a
# replay (origin-locked / blank WebGL re-embed) and the impl emits a <video>
# replay, the <video> substitutes the ref's <canvas> kind — the hero renders
# the ref's OWN recorded motion. Drop "canvas" from missing in that case.
def _canvas_replay_declared() -> bool:
    try:
        plan = json.loads((ref_dir / "canvas-replay-plan.json").read_text(encoding="utf-8"))
        return isinstance(plan, dict) and plan.get("decision") == "canvas-replay"
    except Exception:
        return False

canvas_replay_substituted = False
if "canvas" in missing and impl_kinds.get("video") and _canvas_replay_declared():
    missing = [k for k in missing if k != "canvas"]
    canvas_replay_substituted = True

status = "pass" if not missing else "fail"

artifact = {
    "schemaVersion": 1,
    "status": status,
    "ref": ref_kinds,
    "impl": impl_kinds,
    "missingInImpl": missing,
    "canvasReplaySubstituted": canvas_replay_substituted,
    "implCandidateFiles": [
        str(p.relative_to(impl_dir)) for p in hero_files[:10]
    ],
    "nextAction": (
        f"Add the missing element kinds ({', '.join(missing)}) to the impl "
        "hero component(s). The ref's hero is a layered composite — typically "
        "<video> background, an overlay <button>, an <h1>/<h2> heading, and "
        "a <span> label. The LLM-generated impl usually flattens this into "
        "1-2 layers; restore the full structure by reading the ref hero "
        "selectors in structure.json and matching them element-for-element."
        if missing else "all 4 hero composite kinds present"
    ),
    "rule": (
        "Impl hero region must contain every element kind present in ref "
        "hero (video, button, h1/h2, label span). Missing kinds drop the "
        "gate to fail — LLMs consistently flatten the 4-layer composite "
        "into 1-2 layers, dropping overlay buttons and label spans."
    ),
}
write_artifact(artifact)

if status == "pass":
    print(
        f"hero-composite: PASS — "
        f"video={impl_kinds['video']} button={impl_kinds['button']} "
        f"h1/h2={impl_kinds['h1OrH2']} label={impl_kinds['label']} → {out_path}"
    )
    sys.exit(0)
else:
    print(
        f"hero-composite: FAIL — missing in impl: {', '.join(missing)} → {out_path}"
    )
    sys.exit(1)
PY
