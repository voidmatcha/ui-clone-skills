#!/usr/bin/env bash
# signature-effects-coverage-check.sh — when generation-plan.json declares
# signatureEffects (e.g. a per-character scroll-scrub reveal detected
# deterministically from the ref bundles), the impl MUST actually wire the
# effect's runtime primitives. This closes the gap where the effect is
# detected + declared as a contract but the agent never implements it.
#
# Bounded by design (review): skips entirely when signatureEffects is null/
# empty, so refs without a detected signature effect are unaffected. Only the
# effects that were actually declared are required.
#
# Usage: signature-effects-coverage-check.sh <ref-dir> [<impl-src-dir>]
# Output: <ref-dir>/signature-effects-coverage.json
# Exit: 0 pass/skip, 1 missing coverage, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_SRC_DIR="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: signature-effects-coverage-check.sh <ref-dir> [<impl-src-dir>]" >&2
  exit 2
fi

if [ -z "$IMPL_SRC_DIR" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      if [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT/src" ]; then
        IMPL_SRC_DIR="$IMPL_ROOT/src"
        break
      fi
    fi
  done
fi

OUT_PATH="$REF_DIR/signature-effects-coverage.json"

python3 - "$REF_DIR" "$IMPL_SRC_DIR" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_src = sys.argv[2]
out_path = Path(sys.argv[3])


def write(obj: dict, code: int) -> None:
    out_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print(f"signature-effects-coverage: {obj['status']}")
    sys.exit(code)


plan_p = ref_dir / "generation-plan.json"
try:
    plan = json.loads(plan_p.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    write({"schemaVersion": 1, "status": "skip",
           "reason": "generation-plan.json missing/unreadable"}, 0)

effects = plan.get("signatureEffects")
if not isinstance(effects, list):
    effects = []

# scrollScrub is a deterministic scroll-scrub contract (offset + useTransform
# bands extracted from the bundle): the background scale/zoom and kin. A site
# carrying a `scale` band is the #3 scroll-zoom — the impl MUST wire a
# scroll-bound scale or it ships a static background.
scrub = plan.get("scrollScrub") if isinstance(plan.get("scrollScrub"), dict) else {}
scrub_required = bool(scrub.get("required"))
scrub_has_scale = False
if scrub_required:
    for _site in scrub.get("sites", []) or []:
        if not isinstance(_site, dict):
            continue
        for _t in _site.get("transforms", []) or []:
            if isinstance(_t, dict) and (_t.get("property") or "").startswith("scale"):
                scrub_has_scale = True
                break

if not effects and not scrub_has_scale:
    write({"schemaVersion": 1, "status": "skip",
           "reason": "no signatureEffects or scroll-scrub scale declared — gate does not apply"}, 0)

if not impl_src or not Path(impl_src).is_dir():
    write({"schemaVersion": 1, "status": "skip",
           "reason": "impl/src not found — generation has not produced impl yet"}, 0)

# The deterministically-emitted scroll helpers (emit-scroll-helpers.sh writes
# these into src/lib/). Their DEFINITIONS contain every primitive token —
# useScroll, useSpring, scale, split(" "), data-scroll-scrub, the component name
# itself — so counting them as coverage lets an impl pass while never importing
# them (emitted-but-unwired). Exclude the definition files (only under lib/) so
# the gate measures whether the APP actually wires them.
EMITTED_HELPERS = {
    "ScrollScrub.tsx", "scrollScrubSites.ts", "ScrollWordHighlight.tsx",
    "ScrollReveal.tsx", "SmoothScroll.tsx", "ScrollStateDriver.tsx",
}

# Concatenate impl source (excluding emitted helper definitions).
blob = []
for path in Path(impl_src).rglob("*"):
    if path.is_file() and path.suffix.lower() in {
        ".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".vue", ".svelte"
    }:
        if path.name in EMITTED_HELPERS and path.parent.name == "lib":
            continue
        try:
            blob.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
src = "\n".join(blob)

# Basenames of every impl source file — used to enforce the per-effect
# component contract (a declared signatureEffects[].component must actually
# exist as a file, or its named component must be referenced in src).
impl_basenames = {
    p.name for p in Path(impl_src).rglob("*")
    if p.is_file() and p.suffix.lower() in {
        ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte"
    }
}

# Required runtime-primitive token groups per effect family. The impl must hit
# at least one token from EACH applicable group. Token sets are generous so a
# correct-but-idiomatically-different impl is not false-failed.
SCROLL_BIND = ("scrollYProgress", "useScroll", "ScrollTrigger", "onScroll",
               "scrollY", "useViewportScroll")
# Distinctive per-character split tokens only — NOT bare "letter"/"chars",
# which match common CSS like `letter-spacing`/`letterSpacing` and false-pass.
PER_CHAR = ("totalChars", "perCharacter", "SplitText", "splitText",
            "split(\"\")", "split('')", "charIndex", "chars.map",
            "PerCharacterScrollReveal")
# Per-WORD scroll highlight wiring. Bare split(" ") is far too common (it appears
# incidentally all over a real app), so accept the emitted primitive OR the
# distinctive runtime mechanism (useMotionValueEvent advancing an index over a
# word split) — not a lone space-split.
def _has_per_word_wiring(s: str) -> bool:
    if "ScrollWordHighlight" in s or "data-scroll-word-highlight" in s:
        return True
    return "useMotionValueEvent" in s and ('split(" ")' in s or "split(' ')" in s)

missing = []
for eff in effects:
    if not isinstance(eff, dict):
        continue
    etype = (eff.get("effectType") or eff.get("name") or "").lower()
    trigger = eff.get("trigger") or {}
    anim = eff.get("animation") if isinstance(eff.get("animation"), dict) else {}
    needs_scroll = "scroll" in etype or (isinstance(trigger, dict) and trigger.get("type") == "scroll")
    needs_perword = "per-word" in etype or "word" in etype or bool(anim.get("perWord"))
    # per-word implies a word split, not a char split — don't double-require char.
    needs_perchar = (not needs_perword) and (
        "char" in etype or "per-char" in etype or bool(anim.get("perCharacter"))
    )
    gaps = []
    if needs_scroll and not any(t in src for t in SCROLL_BIND):
        gaps.append("scroll binding (scrollYProgress/useScroll/ScrollTrigger)")
    if needs_perchar and not any(t in src for t in PER_CHAR):
        gaps.append("per-character split (totalChars/SplitText/chars.map)")
    if needs_perword and not _has_per_word_wiring(src):
        gaps.append("per-word scroll highlight (ScrollWordHighlight, or useMotionValueEvent + word split)")
    # Component contract: effects whose name/type does not trip the scroll/
    # per-char/per-word keyword heuristics above (e.g. MarqueeStrip,
    # PlaygroundCanvas, CardStackReveal, StickyGridScrubScene) previously
    # required NOTHING and vacuously passed. For those, require the declared
    # component to be materialized: its file exists under impl/src OR the named
    # component is referenced in src. Only applied when no keyword requirement
    # already covers the effect, so idiomatic inline primitive impls of
    # keyword-detected effects are not false-failed.
    if not (needs_scroll or needs_perword or needs_perchar):
        comp = eff.get("component") or ""
        comp_base = comp.rsplit("/", 1)[-1] if comp else ""
        name = eff.get("name") or ""
        materialized = (comp_base and comp_base in impl_basenames) or (
            bool(name) and re.search(r"\b" + re.escape(name) + r"\b", src) is not None
        )
        if (comp or name) and not materialized:
            gaps.append(
                f"component not implemented: declared {comp or name!r} but no "
                "matching file under impl/src and the named component is not "
                "referenced (build components/ui/<Name>.tsx or wire it)"
            )
    if gaps:
        missing.append({
            "name": eff.get("name"),
            "effectType": eff.get("effectType"),
            "component": eff.get("component"),
            "missingPrimitives": gaps,
        })

# scrollScrub scale coverage: the emitted ScrollScrub primitive (data-scroll-scrub
# / scrollScrubSites) OR an idiomatic hand-rolled scroll-bound scale satisfies it.
# Generous so a correct-but-different impl is not false-failed; fails only on a
# clone that wired no scroll-driven scale at all.
if scrub_has_scale:
    # The scale band (background zoom) must actually be BOUND to scroll — not
    # merely "ScrollScrub is imported" (it may drive only opacity/y, as a real
    # loop did). Require an explicit scale binding: a ScrollScrub scale=/scaleX=
    # /scaleY= prop, OR a hand-rolled scroll useTransform feeding a scale motion
    # value (scale:/{scale}). Bare "scale" (Tailwind scale-*, whileHover) does
    # not count.
    has_scale_prop = re.search(r"\bscale[XY]?\s*=\s*\{", src) is not None
    has_idiomatic_scale = (
        "useTransform" in src
        and ("scrollYProgress" in src or "useScroll" in src)
        and re.search(r"\bscale[XY]?\s*[:}]", src) is not None
    )
    if not (has_scale_prop or has_idiomatic_scale):
        missing.append({
            "name": "scroll-scrub-scale",
            "effectType": "scroll-scrub (background zoom)",
            "component": "src/lib/ScrollScrub.tsx",
            "missingPrimitives": [
                "scroll-bound scale (wrap the scrubbed element in <ScrollScrub> "
                "with the scale band from scrollScrubSites, or useScroll + "
                "useTransform/useSpring onto scale)"
            ],
        })

status = "fail" if missing else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "declaredEffects": len(effects),
    "scrollScrubScaleRequired": scrub_has_scale,
    "missing": missing,
    "rule": (
        "Every generation-plan.signatureEffects entry must be wired in impl "
        "source: scroll-triggered effects need a scroll binding; per-character "
        "effects need a per-character split; a scrollScrub scale band needs a "
        "scroll-bound scale. Detected deterministically from ref bundles — "
        "declaring it then not implementing it ships a clone that is missing the "
        "reference's signature motion."
    ),
}
write(result, 0 if status == "pass" else 1)
PY
