#!/usr/bin/env bash
# motion-coverage-check.sh — fail when the ref bundle uses a motion
# library (GSAP / Framer Motion / Anime / Lottie / Lenis) but the
# impl source has zero actual usage of motion patterns.
#
# Common failure pattern: package.json had only react + react-dom + vite,
# and src/App.jsx had 6 raw transition keyword matches (almost all
# CSS keyword false-positives). No import of framer-motion, gsap,
# lottie, etc. No useScroll / useTransform / IntersectionObserver
# code. The ref's runtime motion was simply not implemented, but
# bundle-impl-coverage passed because it only checks package.json
# vs detected libraries — and if the ref's motion is mostly CSS
# scroll-driven, bundle-map detection of GSAP/Framer also misses it.
# The gap: when ref bundle-map shows ANY motion
# signal AND impl source has near-zero motion code → fail.
#
# Detection:
#   1. Look for motion signals in ref artifacts:
#      - bundle-map.json libraries (lenis, gsap, framer, anime,
#        lottie, webflow-ix2)
#      - transition-spec.json non-empty transitions array
#      - external-sdks.json detected motion SDK
#   2. Look for motion implementation in impl/src:
#      - import statements of motion libraries
#      - hooks: useScroll, useTransform, useSpring, useInView
#      - IntersectionObserver
#      - requestAnimationFrame + scroll handlers
#      - CSS scroll-timeline / @keyframes
#   3. Score: signal_strength vs impl_strength.
#      - signal_strength >= 3 AND impl_strength == 0 → fail
#      - signal_strength >= 5 AND impl_strength < 2 → fail
#
# Usage:
#   motion-coverage-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/motion-coverage.json
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: motion-coverage-check.sh <ref-dir> [<impl-root>]" >&2
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

OUT_PATH="$REF_DIR/motion-coverage.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "motion-coverage: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])


def read_json(p: Path) -> dict | list | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# 1. Score ref-side motion signal strength.
signal_strength = 0
signals: list[str] = []

bm = read_json(ref_dir / "bundle-map.json")
if isinstance(bm, dict):
    libs = bm.get("libraries") or bm.get("detectedLibraries") or []
    if isinstance(libs, list):
        motion_libs = {"gsap", "framer-motion", "framer", "lenis",
                       "anime", "anime.js", "lottie", "lottie-web",
                       "bodymovin", "webflow-ix2", "popmotion",
                       "react-spring", "@react-spring/web", "motion"}
        EXCLUDE = {"emotion", "@emotion/react", "@emotion/styled",
                   "@emotion/css", "@emotion/core"}
        for lib in libs:
            if isinstance(lib, dict):
                name = (lib.get("name") or lib.get("id") or "").lower()
            else:
                name = str(lib).lower()
            if name in EXCLUDE or any(name.startswith(e) for e in EXCLUDE):
                continue
            matched = None
            for ml in motion_libs:
                # Package boundary: exact match, or starts with
                # "<ml>/", "<ml>@", or ends with that lib token.
                if (
                    name == ml
                    or name.startswith(f"{ml}/")
                    or name.startswith(f"{ml}@")
                    or name.startswith(f"@{ml}/")
                    or f"/{ml}" in name
                ):
                    matched = ml
                    break
            if matched:
                signal_strength += 2
                signals.append(f"bundle-map:{matched}")

ts = read_json(ref_dir / "transition-spec.json")
if isinstance(ts, dict):
    transitions = ts.get("transitions") or []
    if isinstance(transitions, list) and len(transitions) > 0:
        # Each declared transition is a hard signal that motion code
        # should exist in impl.
        signal_strength += min(5, len(transitions))
        signals.append(f"transition-spec: {len(transitions)} entries")

es = read_json(ref_dir / "external-sdks.json")
if isinstance(es, dict):
    detected = es.get("detected")
    # `detected` is a dict {lib: {matches: n}}; older payloads used a list of
    # lib-name strings. Normalize both to a list of names (the prior code only
    # handled the list shape and silently dropped all dict-shaped SDK signal).
    if isinstance(detected, dict):
        detected_names = list(detected.keys())
    elif isinstance(detected, list):
        detected_names = detected
    else:
        detected_names = []
    motion_sdks = {"gsap", "lottie", "bodymovin", "framer", "anime"}
    for d in detected_names:
        ds = str(d).lower()
        if any(m in ds for m in motion_sdks):
            signal_strength += 1
            signals.append(f"external-sdk:{ds}")

# Step H integration — categorical motion_signature from
# transition-categorize.sh is a high-confidence single-hit signal.
# A site declaring a real motion feel (springy / scrubbed / snappy /
# gentle) demands impl coverage regardless of the accumulated
# signal_strength score. Catches the gap where a site has only external-sdk
# evidence (weight +1) below the >=3 threshold
# but still has substantive motion the impl skipped.
strong_feel = False
strong_feel_label = ""
if isinstance(ts, dict):
    msig = ts.get("motion_signature") or {}
    if isinstance(msig, dict):
        dominant = str(msig.get("dominant_feel") or "")
        if dominant in {"springy", "scrubbed", "snappy", "gentle"}:
            strong_feel = True
            strong_feel_label = dominant
            signals.append(f"motion_signature.dominant_feel:{dominant}")


# 1b. Library-driven detection — same animation-library allowlist as
# library-usage-check.sh. When the ref's motion runs through a JS animation
# library (evidenced in bundle-map.json / external-sdks.json), impl motion
# coverage must come from that library's usage. Two impl signals then stop
# being evidence of reproduced motion:
#   * bare requestAnimationFrame() — the hand-rolled shim used to fake
#     useScroll/useTransform with scale();
#   * @keyframes / animation that live only in the mirrored ref CSS
#     (from-ref/, ref-css/) — a byte copy of the ref stylesheet that does not
#     re-create the library's runtime scrub/spring.
# For refs WITHOUT library evidence (vanilla-JS / pure-CSS), rAF and mirrored
# keyframes still count exactly as before — those refs legitimately implement
# motion that way.
ANIM_LIBS = [
    ("framer-motion", ("framer", "framermotion", "motion-like", "usescroll",
                       "usetransform", "scrollyprogress", "motionvalue",
                       "animatepresence")),
    ("gsap", ("gsap", "scrolltrigger", "scrollsmoother")),
    ("lenis", ("lenis",)),
    ("lottie", ("lottie", "bodymovin")),
    ("three", ("three-like", "react-three", "threejs")),
    ("swiper", ("swiper",)),
    ("embla", ("embla",)),
    ("splide", ("splide",)),
    ("keen-slider", ("keen-slider", "keenslider")),
    ("react-spring", ("react-spring", "reactspring")),
    ("popmotion", ("popmotion",)),
    ("locomotive-scroll", ("locomotive",)),
]


def _library_tokens() -> list[str]:
    tokens: list[str] = []
    if isinstance(bm, dict):
        chunks = bm.get("chunks")
        chunk_iter = chunks.values() if isinstance(chunks, dict) else (
            chunks if isinstance(chunks, list) else [])
        for ch in chunk_iter:
            if isinstance(ch, dict):
                tokens += [str(x) for x in (ch.get("libs") or []) if isinstance(x, str)]
        libraries = bm.get("libraries")
        if isinstance(libraries, dict):
            tokens += [str(k) for k, v in libraries.items() if v]
        elif isinstance(libraries, list):
            for lib in libraries:
                tokens.append(
                    str(lib.get("name") or lib.get("id") or "")
                    if isinstance(lib, dict) else str(lib))
        for extra in ("detectedLibraries",):
            val = bm.get(extra)
            if isinstance(val, list):
                tokens += [str(x) for x in val]
        evidence = bm.get("evidence")
        if isinstance(evidence, dict):
            tokens += [str(k) for k, v in evidence.items() if v]
        notes = bm.get("notes")
        if isinstance(notes, str):
            tokens.append(notes)
    if isinstance(es, dict):
        detected = es.get("detected")
        if isinstance(detected, dict):
            tokens += [str(k) for k in detected.keys()]
        elif isinstance(detected, list):
            tokens += [str(k) for k in detected]
        for k, v in es.items():
            if k != "detected" and v is True:
                tokens.append(str(k))
    return tokens


_token_blob = " ".join(_library_tokens()).lower()
ref_motion_libraries = sorted(
    name for name, needles in ANIM_LIBS if any(n in _token_blob for n in needles)
)
ref_library_driven = bool(ref_motion_libraries)

# Mirror dirs whose CSS is a byte copy of the ref stylesheet — same set
# transition-spec-coverage.sh excludes (from-ref/, ref-css/, plus any
# UI_CLONE_GENERATED_EVIDENCE_DIRS override). Not counted as impl-authored
# motion for library-driven refs.
MIRROR_DIRS = {"from-ref", "ref-css"}
for _d in re.split(r"[,\s]+", os.environ.get("UI_CLONE_GENERATED_EVIDENCE_DIRS", "")):
    if _d:
        MIRROR_DIRS.add(_d)


# 2. Score impl-side motion strength.
impl_strength = 0
impl_signals: list[str] = []
SRC_DIRS = [impl_root / "src", impl_root / "app", impl_root / "pages"]
SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs"}
CSS_SUFFIXES = {".css", ".scss", ".sass", ".less", ".module.css",
                ".vue", ".svelte", ".astro"}

# CSS-side motion patterns. Each match adds 1 to impl_strength.
CSS_KEYFRAMES_RE = re.compile(r"@keyframes\s+[a-zA-Z_][\w-]*\s*\{", re.IGNORECASE)
CSS_ANIM_RE = re.compile(
    r"\banimation(?:-name|-duration|-timing-function)?\s*:\s*"
    r"(?!none\b|inherit\b|initial\b|unset\b)[a-z]", re.IGNORECASE,
)
CSS_TRANS_RE = re.compile(
    r"\btransition(?:-property|-duration)?\s*:\s*"
    r"(?!none\b|all\s+0s\b|inherit\b)[a-z]", re.IGNORECASE,
)
CSS_SCROLL_TIMELINE_RE = re.compile(
    r"\b(?:scroll-timeline|animation-timeline|view-timeline)\s*:",
    re.IGNORECASE,
)

# Motion library imports (named or default).
IMPORT_RE = re.compile(
    r"\bfrom\s+['\"]("
    r"framer-motion|gsap(?:/[^'\"]*)?|@react-spring/web|@react-spring/.+|"
    r"lottie-web|lottie-react|@lottiefiles/.+|@dotlottie/.+|"
    r"anime|animejs|popmotion|@studio-freight/lenis|lenis|"
    r"motion|motion/react"
    r")['\"]"
)
# Hook usage.
HOOK_RE = re.compile(
    r"\b("
    r"useScroll|useTransform|useSpring|useInView|useMotionValue|"
    r"useAnimation|useAnimationControls|useViewportScroll|"
    r"useScrollPosition|useGSAP"
    r")\s*\("
)
# Browser APIs for scroll-driven motion. requestAnimationFrame is split out
# (RAF_RE) so it can be discounted for library-driven refs — a bare rAF loop is
# the classic shim used to fake a library's scroll-scrub. IntersectionObserver /
# ScrollTimeline stay counted (legitimate native reveal / scroll-driven APIs).
SCROLL_API_RE = re.compile(
    r"\b(IntersectionObserver|ScrollTimeline)\s*\("
)
RAF_RE = re.compile(r"\brequestAnimationFrame\s*\(")
# Inline GSAP-style calls.
GSAP_CALL_RE = re.compile(
    r"\b(gsap|ScrollTrigger)\s*\.\s*(to|from|fromTo|set|timeline|create|registerPlugin)\s*\("
)


rejected_signals: list[dict] = []


def award(amount: int, sig: str) -> None:
    global impl_strength
    impl_strength += amount
    impl_signals.append(sig)


def reject(sig: str, reason: str) -> None:
    rejected_signals.append({"signal": sig, "reason": reason})


for sd in SRC_DIRS:
    if not sd.is_dir():
        continue
    for p in sd.rglob("*"):
        if not p.is_file():
            continue
        if any(part in {"node_modules", ".next", "dist", "build"} for part in p.parts):
            continue
        is_mirror = any(part in MIRROR_DIRS for part in p.parts)
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if p.suffix in SUFFIXES:
            for m in IMPORT_RE.finditer(text):
                award(2, f"{p.name}:import {m.group(1)}")
            for m in HOOK_RE.finditer(text):
                award(1, f"{p.name}:hook {m.group(1)}")
            for m in SCROLL_API_RE.finditer(text):
                award(1, f"{p.name}:api {m.group(1)}")
            for _ in RAF_RE.finditer(text):
                sig = f"{p.name}:api requestAnimationFrame"
                if ref_library_driven:
                    reject(sig, "bare requestAnimationFrame shim not counted "
                                "for a library-driven ref")
                else:
                    award(1, sig)
            for m in GSAP_CALL_RE.finditer(text):
                award(1, f"{p.name}:gsap {m.group(1)}.{m.group(2)}")
        if p.suffix in CSS_SUFFIXES:
            scope = text
            if p.suffix in {".vue", ".svelte", ".astro"}:
                # Pull only <style> block contents for these.
                style_blocks = re.findall(
                    r"<style[^>]*>([\s\S]*?)</style>", text, re.IGNORECASE,
                )
                scope = "\n".join(style_blocks)
            # For a library-driven ref, CSS motion inside a mirrored ref-CSS dir
            # is a byte copy of the ref stylesheet — not impl-authored motion, so
            # it must not count toward coverage.
            mirror_discount = ref_library_driven and is_mirror
            kf = CSS_KEYFRAMES_RE.findall(scope)
            if kf:
                sig = f"{p.name}:keyframes x{len(kf)}"
                if mirror_discount:
                    reject(sig, "@keyframes in mirrored ref CSS not counted "
                                "for a library-driven ref")
                else:
                    award(min(3, len(kf)), sig)
            anim = CSS_ANIM_RE.findall(scope)
            if anim:
                sig = f"{p.name}:animation x{len(anim)}"
                if mirror_discount:
                    reject(sig, "animation in mirrored ref CSS not counted "
                                "for a library-driven ref")
                else:
                    award(min(2, len(anim)), sig)
            trans = CSS_TRANS_RE.findall(scope)
            if trans:
                sig = f"{p.name}:transitions x{len(trans)}"
                if mirror_discount:
                    reject(sig, "transition in mirrored ref CSS not counted "
                                "for a library-driven ref")
                else:
                    # Transition declarations are common (hover etc) — cap
                    # contribution so one .css file doesn't dominate. Only log a
                    # signal string when the count is notable (>= 5), matching
                    # the pre-existing behavior.
                    impl_strength += min(2, len(trans) // 5)
                    if len(trans) >= 5:
                        impl_signals.append(sig)
            st = CSS_SCROLL_TIMELINE_RE.findall(scope)
            if st:
                sig = f"{p.name}:scroll-timeline"
                if mirror_discount:
                    reject(sig, "scroll-timeline in mirrored ref CSS not counted "
                                "for a library-driven ref")
                else:
                    award(2, sig)


# 3. Score → status.
violations: list[dict] = []
if signal_strength >= 3 and impl_strength == 0:
    violations.append({
        "kind": "ref-has-motion-impl-has-none",
        "refSignalStrength": signal_strength,
        "implMotionStrength": impl_strength,
        "refSignals": signals[:10],
        "detail": (
            "Ref bundle / spec / SDK evidence declares motion "
            "(score >= 3) but impl source has zero motion-library "
            "imports, scroll hooks, IntersectionObserver, or GSAP "
            "calls. Implementation skipped motion entirely."
        ),
    })
elif signal_strength >= 5 and impl_strength < 2:
    violations.append({
        "kind": "ref-has-heavy-motion-impl-has-minimal",
        "refSignalStrength": signal_strength,
        "implMotionStrength": impl_strength,
        "refSignals": signals[:10],
        "implSignals": impl_signals[:10],
        "detail": (
            "Ref has heavy motion evidence (score >= 5) but impl "
            "has < 2 motion-implementation signals — likely a "
            "tokenistic single transition without the rest of the "
            "ref's motion surface."
        ),
    })
elif strong_feel and impl_strength == 0:
    violations.append({
        "kind": "ref-declares-feel-impl-has-none",
        "refDominantFeel": strong_feel_label,
        "refSignalStrength": signal_strength,
        "implMotionStrength": impl_strength,
        "refSignals": signals[:10],
        "detail": (
            f"Ref transition-spec.motion_signature.dominant_feel="
            f"{strong_feel_label!r} declares real motion character, "
            "but impl source has zero motion implementation. This is "
            "a high-confidence single-hit signal that bypasses the "
            "accumulator threshold for sites whose motion evidence "
            "lives only in external-sdks / signature categorization."
        ),
    })


status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "refSignalStrength": signal_strength,
    "implMotionStrength": impl_strength,
    "refSignals": signals[:20],
    "implSignals": impl_signals[:20],
    # Provenance superset: whether the ref motion is library-driven, which
    # animation libraries were detected, and every impl signal that was
    # DISCOUNTED (rAF shim / mirrored-ref-CSS motion) so rejected coverage is
    # auditable rather than silently dropped.
    "refIsLibraryDriven": ref_library_driven,
    "refMotionLibraries": ref_motion_libraries,
    "rejectedImplSignals": rejected_signals[:20],
    "violations": violations,
    "rule": (
        "When ref bundle-map / transition-spec / external-sdks "
        "evidence motion (score >= 3), impl source must show "
        "matching motion implementation (imports, hooks, "
        "IntersectionObserver, GSAP calls). Score 0 with ref >= 3, "
        "or score < 2 with ref >= 5, fails. For a library-driven ref "
        "(animation lib in bundle-map/external-sdks), bare "
        "requestAnimationFrame and @keyframes/animation in mirrored ref "
        "CSS (from-ref/, ref-css/) do NOT count as impl motion; refs "
        "without library evidence keep counting them."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"motion-coverage: ref={signal_strength} impl={impl_strength} "
    f"→ {status}"
)
sys.exit(0 if status == "pass" else 1)
PY
