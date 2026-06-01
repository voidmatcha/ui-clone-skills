#!/usr/bin/env bash
# scroll-engine-parity-check.sh — fail when the ref uses a scroll-
# driven motion engine (Lenis + GSAP ScrollTrigger / Framer useScroll
# / native scroll-timeline) but the impl uses a fundamentally
# different motion CLASS that cannot reproduce scroll-pinned or
# scroll-scrubbed behavior.
#
# Background: motion-coverage-check rewards ANY motion code in impl,
# so a clone using bare IntersectionObserver + CSS transitions can
# pass even when the ref uses gsap.scrollTrigger({pin, scrub}). The
# visual result diverges fundamentally: IO is binary (in-view /
# out-of-view), scrub is continuous (animation progress tied to
# scroll position). Same with pin: IO can't lock an element to
# viewport while the user scrolls through an animation timeline.
#
# This gate enforces ENGINE CLASS parity:
#   Ref class signals:
#     - bundle-map.json libraries containing gsap + ScrollTrigger
#     - bundle-extraction.json gsap.create / ScrollTrigger.create
#     - transition-spec.json transitions with trigger: scroll-scrub
#     - animation-runtime-dump.json with scrub-progress samples
#     - Lenis instantiation (smooth-scroll wrapper)
#     - native scroll-timeline / animation-timeline CSS rules
#   Impl class signals:
#     - import gsap + ScrollTrigger
#     - import @studio-freight/lenis OR lenis
#     - useScroll / useTransform / useMotionValueEvent (framer-motion)
#     - element.addEventListener('scroll', ...) + transform
#     - CSS scroll-timeline / animation-timeline
#
# Pass: ref class is a subset of impl class OR ref has no scroll-
# driven motion.
# Fail: ref has scroll-scrub OR scroll-pin OR smooth-scroll, impl
# has only binary IO / CSS transition / RAF-without-scroll-source.
#
# Usage:
#   scroll-engine-parity-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/scroll-engine-parity.json
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: scroll-engine-parity-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

OUT_PATH="$REF_DIR/scroll-engine-parity.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "scroll-engine-parity: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ── 1. Detect ref-side scroll-engine class signals ──
ref_classes: set[str] = set()
ref_evidence: list[str] = []

# Library signals from bundle-map.
bm = read_json(ref_dir / "bundle-map.json")
if isinstance(bm, dict):
    libs = bm.get("libraries") or bm.get("detectedLibraries") or []
    if isinstance(libs, list):
        for entry in libs:
            name = entry.get("name", "") if isinstance(entry, dict) else str(entry)
            n = name.lower()
            if n in {"gsap", "gsap/scrolltrigger"} or "scrolltrigger" in n:
                ref_classes.add("gsap-scrolltrigger")
                ref_evidence.append(f"bundle-map:{name}")
            if "lenis" in n:
                ref_classes.add("lenis-smooth-scroll")
                ref_evidence.append(f"bundle-map:{name}")
            if "framer-motion" in n or n == "motion":
                ref_classes.add("framer-motion")
                ref_evidence.append(f"bundle-map:{name}")

# Inline construction signals from bundle-extraction (when available).
be = read_json(ref_dir / "bundle-extraction.json")
if isinstance(be, dict):
    constructions = be.get("constructions") or be.get("scrollTriggers") or []
    if isinstance(constructions, list):
        for c in constructions:
            if not isinstance(c, dict):
                continue
            kind = (c.get("kind") or c.get("type") or "").lower()
            if "scrolltrigger" in kind:
                ref_classes.add("gsap-scrolltrigger")
                if c.get("pin"):
                    ref_classes.add("scroll-pin")
                    ref_evidence.append("bundle-extraction:pin=true")
                if c.get("scrub") not in (None, False, 0):
                    ref_classes.add("scroll-scrub")
                    ref_evidence.append("bundle-extraction:scrub")
            if "lenis" in kind:
                ref_classes.add("lenis-smooth-scroll")

# Transition spec signals.
ts = read_json(ref_dir / "transition-spec.json")
if isinstance(ts, dict):
    for t in (ts.get("transitions") or []):
        if not isinstance(t, dict):
            continue
        trigger = (t.get("trigger") or "").lower()
        if "scrub" in trigger or "progress" in trigger:
            ref_classes.add("scroll-scrub")
            ref_evidence.append(f"transition-spec:{t.get('id','?')}:scrub")
        if "pin" in trigger or "sticky-scrub" in trigger:
            ref_classes.add("scroll-pin")
            ref_evidence.append(f"transition-spec:{t.get('id','?')}:pin")
        if "scroll" in trigger and "scrub" not in trigger:
            ref_classes.add("scroll-driven")

# Animation runtime dump (live samples).
ard = read_json(ref_dir / "animation-runtime-dump.json")
if isinstance(ard, dict):
    if ard.get("scrollTrigger") or ard.get("scrollTriggers"):
        ref_classes.add("gsap-scrolltrigger")
        ref_evidence.append("animation-runtime:scrollTrigger")

# Scroll-engine extraction artifact. This is the canonical Step 5c/bundle
# signal, and often contains the only normalized Lenis/ScrollTrigger pin/scrub
# evidence when bundle-map failed to name the library precisely.
se = read_json(ref_dir / "scroll-engine.json")
if isinstance(se, dict):
    se_text = json.dumps(se, ensure_ascii=False).lower()
    if "scrolltrigger" in se_text or ("gsap" in se_text and "scroll" in se_text):
        ref_classes.add("gsap-scrolltrigger")
        ref_evidence.append("scroll-engine:ScrollTrigger")
    if "lenis" in se_text or "locomotive" in se_text or "scrollsmoother" in se_text:
        ref_classes.add("lenis-smooth-scroll")
        ref_evidence.append("scroll-engine:smooth-scroll")
    if re.search(r'"scrub"\s*:\s*true|\bscrub\s*[:=]|\bsticky-scrub\b|\bscroll-scrub\b', se_text):
        ref_classes.add("scroll-scrub")
        ref_evidence.append("scroll-engine:scrub")
    if re.search(r'"pin"\s*:\s*true|\bpin\s*[:=]|\bsticky-scrub\b|\bscroll-pin\b', se_text):
        ref_classes.add("scroll-pin")
        ref_evidence.append("scroll-engine:pin")
    if "scroll" in se_text:
        ref_classes.add("scroll-driven")
        ref_evidence.append("scroll-engine:scroll")

# CSS scroll-timeline in ref bundles.
bundles_css = ref_dir / "bundles"
if bundles_css.is_dir():
    for css_path in bundles_css.glob("*.css"):
        text = read_text(css_path)
        if re.search(r"\b(?:scroll-timeline|animation-timeline|view-timeline)\s*:",
                     text, re.IGNORECASE):
            ref_classes.add("native-scroll-timeline")
            ref_evidence.append(f"bundle-css:{css_path.name}")
            break

# ── 2. Detect impl-side scroll-engine class signals ──
impl_classes: set[str] = set()
impl_evidence: list[str] = []

# package.json deps.
pkg = read_json(impl_root / "package.json")
deps: dict[str, str] = {}
if isinstance(pkg, dict):
    for k in ("dependencies", "devDependencies"):
        d = pkg.get(k) or {}
        if isinstance(d, dict):
            deps.update({kk: str(vv) for kk, vv in d.items()})
if "gsap" in deps:
    impl_classes.add("gsap-scrolltrigger")  # conservative — gsap usually includes ScrollTrigger
    impl_evidence.append("pkg:gsap")
if "lenis" in deps or "@studio-freight/lenis" in deps:
    impl_classes.add("lenis-smooth-scroll")
    impl_evidence.append("pkg:lenis")
if "framer-motion" in deps or "motion" in deps:
    impl_classes.add("framer-motion")
    impl_evidence.append("pkg:framer-motion")

# Source code signals.
SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs"}
CSS_SUFFIXES = {".css", ".scss", ".sass", ".less", ".module.css",
                ".vue", ".svelte", ".astro"}
SRC_DIRS = [impl_root / "src", impl_root / "app", impl_root / "pages"]

GSAP_USE_RE = re.compile(
    r"(?:from\s+['\"]gsap(?:/[^'\"]*)?['\"])"
    r"|(?:gsap\s*\.\s*(?:to|from|fromTo|timeline|registerPlugin))"
    r"|(?:ScrollTrigger\s*\.\s*create)"
    r"|(?:scrollTrigger\s*:)"
)
FRAMER_RE = re.compile(
    r"useScroll\s*\(|useTransform\s*\(|useMotionValueEvent\s*\(|useScrollMotion"
)
NATIVE_SCROLL_HANDLER = re.compile(
    r"addEventListener\s*\(\s*['\"]scroll['\"]|window\.onscroll\s*="
)
CSS_TIMELINE_RE = re.compile(
    r"\b(?:scroll-timeline|animation-timeline|view-timeline)\s*:",
    re.IGNORECASE,
)
LENIS_USE_RE = re.compile(
    r"new\s+Lenis|@studio-freight/lenis|from\s+['\"]lenis['\"]"
)
STICKY_PIN_RE = re.compile(
    r"position\s*:\s*sticky\b"
)

for sd in SRC_DIRS:
    if not sd.is_dir():
        continue
    for p in sd.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {"node_modules", ".next", "dist", "build"} for part in p.parts):
            continue
        text = read_text(p)
        if not text:
            continue
        if p.suffix in SUFFIXES:
            if GSAP_USE_RE.search(text):
                impl_classes.add("gsap-scrolltrigger")
                impl_evidence.append(f"{p.name}:gsap")
            if FRAMER_RE.search(text):
                impl_classes.add("framer-motion")
                impl_evidence.append(f"{p.name}:framer")
            if NATIVE_SCROLL_HANDLER.search(text):
                impl_classes.add("native-scroll-handler")
                impl_evidence.append(f"{p.name}:onscroll")
            if LENIS_USE_RE.search(text):
                impl_classes.add("lenis-smooth-scroll")
                impl_evidence.append(f"{p.name}:lenis")
        if p.suffix in CSS_SUFFIXES:
            if CSS_TIMELINE_RE.search(text):
                impl_classes.add("native-scroll-timeline")
                impl_evidence.append(f"{p.name}:scroll-timeline")
            if STICKY_PIN_RE.search(text):
                impl_classes.add("css-sticky")  # weaker than scroll-pin but credits
                impl_evidence.append(f"{p.name}:position:sticky")


# ── 3. Compare classes ──
# Equivalence map — impl satisfies ref needs when:
#   scroll-scrub:        framer-motion OR gsap-scrolltrigger OR native-scroll-timeline
#   scroll-pin:          gsap-scrolltrigger OR css-sticky (with scroll handler) OR native-scroll-timeline
#   scroll-driven:       framer-motion OR gsap-scrolltrigger OR native-scroll-handler OR native-scroll-timeline
#   lenis-smooth-scroll: lenis-smooth-scroll
#   gsap-scrolltrigger:  gsap-scrolltrigger (other libs don't replicate full API)
#   framer-motion:       framer-motion
#   native-scroll-timeline: native-scroll-timeline


def impl_satisfies(ref_class: str) -> tuple[bool, str]:
    if ref_class == "scroll-scrub":
        sat = bool(impl_classes & {"framer-motion", "gsap-scrolltrigger", "native-scroll-timeline"})
        return sat, "needs progress-bound scroll engine (framer / gsap / native-scroll-timeline)"
    if ref_class == "scroll-pin":
        sat = bool(impl_classes & {"gsap-scrolltrigger", "native-scroll-timeline"})
        return sat, "needs pin engine (gsap ScrollTrigger or scroll-timeline); css-sticky alone cannot scrub"
    if ref_class == "scroll-driven":
        sat = bool(impl_classes & {"framer-motion", "gsap-scrolltrigger",
                                   "native-scroll-handler", "native-scroll-timeline"})
        return sat, "needs any scroll-driven engine"
    if ref_class == "lenis-smooth-scroll":
        sat = "lenis-smooth-scroll" in impl_classes
        return sat, "ref uses Lenis smooth-scroll wrapper; impl must include lenis or equivalent"
    return ref_class in impl_classes, f"ref class `{ref_class}` not present in impl"


violations: list[dict] = []
for rc in sorted(ref_classes):
    sat, detail = impl_satisfies(rc)
    if not sat:
        violations.append({
            "kind": "missing-scroll-engine-class",
            "refClass": rc,
            "implClasses": sorted(impl_classes),
            "detail": detail,
        })


status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "refClasses": sorted(ref_classes),
    "implClasses": sorted(impl_classes),
    "refEvidence": ref_evidence[:20],
    "implEvidence": impl_evidence[:20],
    "violations": violations,
    "rule": (
        "Ref-detected scroll engine classes (gsap-scrolltrigger / "
        "scroll-scrub / scroll-pin / lenis-smooth-scroll / "
        "framer-motion / native-scroll-timeline) must be satisfied "
        "by an equivalent class in impl. CSS transitions + bare "
        "IntersectionObserver do NOT replicate progress-bound "
        "scroll-scrub or sticky-pin behavior — choosing them when "
        "the ref uses gsap.scrollTrigger({pin, scrub}) is a "
        "fundamental motion-class mismatch."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"scroll-engine-parity: ref={{{','.join(sorted(ref_classes)) or 'none'}}} "
    f"impl={{{','.join(sorted(impl_classes)) or 'none'}}} → {status}"
)
sys.exit(0 if status == "pass" else 1)
PY
