#!/usr/bin/env bash
# library-usage-check.sh — fail when a ref-detected animation library is
# DECLARED/installed but never IMPORTED in impl source (the rAF-shim loophole).
#
# bundle-impl-coverage-check.sh already proves a detected lib has a matching
# package.json install. That is necessary but not sufficient: on the ebay run,
# framer-motion was declared in package.json yet never imported — the entire
# useScroll/useTransform surface was hand-approximated with a requestAnimationFrame
# shim and scale(). package.json presence alone therefore satisfied mirroring
# while the ref's motion engine was absent from the shipped code.
#
# This check closes that gap: for every ANIMATION library the ref actually uses
# (detected in bundle-map.json / external-sdks.json), require at least one real
# import/require of that package's module in impl source. Zero import hits = fail,
# regardless of whether the package is installed.
#
# Usage: library-usage-check.sh <ref-dir> [<impl-root>]
#   ref-dir     the canonical ref dir
#   impl-root   impl project root; auto-detected via find-impl-root.sh if omitted
#
# Reads:
#   <ref-dir>/bundle-map.json      — chunks[].libs[] / libraries{} / evidence{} / notes
#   <ref-dir>/external-sdks.json   — detected{} or detected[] SDK names
#   <impl-root>/src/**             — import/require module specifiers
#   <impl-root>/package.json       — install status (for the remediation message)
#
# Writes:
#   <ref-dir>/library-usage.json   — schemaVersion 1, status, detectedLibs[],
#                                     importedLibs[], unusedLibs[], implRoot, reason
#
# Pass criteria:
#   pass  — every detected animation library has >=1 import/require in impl source,
#           OR no animation library is detected (static build, nothing to verify)
#   fail  — at least one detected animation library has zero import hits (dead wire)
#   skip  — neither ref evidence file present, OR impl source not found

set -euo pipefail

REF_DIR="${1:?Usage: library-usage-check.sh <ref-dir> [<impl-root>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

BUNDLE_MAP="$REF_DIR/bundle-map.json"
EXTERNAL_SDKS="$REF_DIR/external-sdks.json"
OUT="$REF_DIR/library-usage.json"

IMPL_ROOT="${2:-}"
IMPL_SRC=""
IMPL_PKG=""
if [ -n "$IMPL_ROOT" ]; then
  IMPL_SRC="$IMPL_ROOT/src"
  IMPL_PKG="$IMPL_ROOT/package.json"
else
  RESOLVER="${PLUGIN_ROOT:-$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null || true)
    IMPL_ROOT=$(printf '%s\n' "$RESOLVED" | sed -n '1p')
    IMPL_SRC=$(printf '%s\n' "$RESOLVED" | sed -n '2p')
    IMPL_PKG=$(printf '%s\n' "$RESOLVED" | sed -n '3p')
  fi
fi

# Scan the resolved src dir when present, else the impl root.
SCAN_DIR=""
if [ -n "$IMPL_SRC" ] && [ -d "$IMPL_SRC" ]; then
  SCAN_DIR="$IMPL_SRC"
elif [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ]; then
  SCAN_DIR="$IMPL_ROOT"
fi

python3 - "$OUT" "$BUNDLE_MAP" "$EXTERNAL_SDKS" "$SCAN_DIR" "$IMPL_PKG" "$IMPL_ROOT" <<'PY'
import json
import os
import re
import sys

out_path, bundle_map_path, external_sdks_path, scan_dir, impl_pkg, impl_root = sys.argv[1:7]

# Curated ANIMATION/interaction libraries: (display, detection substrings, npm
# package candidates). Only libraries in this allowlist are enforced — unknown
# SDKs (analytics, tag managers) are ignored so a clone that legitimately omits
# them is never failed. Detection substrings are matched case-insensitively
# against raw signal tokens from bundle-map.json and external-sdks.json; a lib
# counts as imported when any candidate package is the module specifier or its
# path prefix (e.g. `gsap/ScrollTrigger` -> gsap, `motion/react` -> motion).
ANIM_LIBS = [
    ("framer-motion",
     ["framer", "framermotion", "motion-like", "usescroll", "usetransform",
      "scrollyprogress", "motionvalue", "useanimation", "animatepresence"],
     ["framer-motion", "motion"]),
    ("gsap",
     ["gsap", "scrolltrigger", "scrollsmoother"],
     ["gsap", "@gsap/react"]),
    ("lenis",
     ["lenis"],
     ["lenis", "@studio-freight/lenis"]),
    ("lottie",
     ["lottie", "bodymovin"],
     ["lottie-web", "lottie-react", "@lottiefiles/react-lottie-player",
      "@lottiefiles/dotlottie-react"]),
    ("three",
     ["three-like", "react-three", "threejs"],
     ["three", "@react-three/fiber", "@react-three/drei"]),
    ("swiper",
     ["swiper"],
     ["swiper"]),
    ("embla",
     ["embla"],
     ["embla-carousel", "embla-carousel-react"]),
    ("splide",
     ["splide"],
     ["@splidejs/splide", "@splidejs/react-splide"]),
    ("keen-slider",
     ["keen-slider", "keenslider"],
     ["keen-slider"]),
    ("react-spring",
     ["react-spring", "reactspring"],
     ["@react-spring/web", "react-spring"]),
    ("popmotion",
     ["popmotion"],
     ["popmotion"]),
    ("locomotive-scroll",
     ["locomotive"],
     ["locomotive-scroll"]),
]

# Bundle-map `libraries{}` / `evidence{}` keys use camelCase names; fold them in
# so both the older chunks[].libs[] and newer flat schemas are covered.
LIBRARY_KEY_ALIASES = {
    "gsap": "gsap", "scrolltrigger": "scrolltrigger", "framermotion": "framermotion",
    "motion": "motion-like", "lenis": "lenis", "three": "three-like",
    "reactspring": "react-spring", "popmotion": "popmotion", "swiper": "swiper",
    "lottie": "lottie", "bodymovin": "bodymovin",
}


def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ── 1. Gather raw signal tokens from both ref evidence sources ────────────────
tokens = []
have_ref_evidence = False

bm = _read_json(bundle_map_path)
if bm is not None:
    have_ref_evidence = True
    if isinstance(bm, dict):
        chunks = bm.get("chunks")
        if isinstance(chunks, dict):
            for ch in chunks.values():
                if isinstance(ch, dict):
                    tokens += [str(x) for x in (ch.get("libs") or []) if isinstance(x, str)]
        libraries = bm.get("libraries")
        if isinstance(libraries, dict):
            for key, present in libraries.items():
                if present:
                    tokens.append(LIBRARY_KEY_ALIASES.get(str(key).lower(), str(key)))
        evidence = bm.get("evidence")
        if isinstance(evidence, dict):
            for key, hits in evidence.items():
                if hits:
                    tokens.append(LIBRARY_KEY_ALIASES.get(str(key).lower(), str(key)))
        notes = bm.get("notes")
        if isinstance(notes, str):
            tokens.append(notes)

es = _read_json(external_sdks_path)
if es is not None:
    have_ref_evidence = True
    detected = es.get("detected") if isinstance(es, dict) else None
    if isinstance(detected, dict):
        tokens += [str(k) for k in detected.keys()]
    elif isinstance(detected, list):
        tokens += [str(k) for k in detected]
    # Some payloads use flat boolean keys ({"gsap": true, ...}).
    if isinstance(es, dict):
        for key, val in es.items():
            if key != "detected" and val is True:
                tokens.append(LIBRARY_KEY_ALIASES.get(str(key).lower(), str(key)))

token_blob = " ".join(tokens).lower()

# ── 2. Resolve which animation libraries the ref actually uses ────────────────
detected_libs = []
for display, needles, candidates in ANIM_LIBS:
    if any(n in token_blob for n in needles):
        detected_libs.append({"name": display, "anyOf": candidates})


def _write(status, reason, imported=None, unused=None):
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "schemaVersion": 1,
            "status": status,
            "detectedLibs": [d["name"] for d in detected_libs],
            "importedLibs": imported or [],
            "unusedLibs": unused or [],
            "implRoot": impl_root,
            "reason": reason,
        }, fh, indent=2)


if not have_ref_evidence:
    _write("skip", "no bundle-map.json / external-sdks.json — nothing to verify against")
    sys.exit(0)

if not detected_libs:
    # Ref evidence exists but declares no enforced animation library — a static
    # or non-motion clone has nothing to import. PASS (not skip): the dispatcher
    # counts skip as failure, and "no animation lib" is unambiguously success.
    _write("pass", "no enforced animation library detected in ref evidence")
    sys.exit(0)

if not scan_dir or not os.path.isdir(scan_dir):
    _write("skip", "impl source not found — scaffold impl or pass <impl-root> explicitly")
    sys.exit(0)

# ── 3. Collect import/require module specifiers from impl source ──────────────
PRUNE = {"node_modules", ".next", ".turbo", "dist", "build", ".git"}
EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
SPEC_RES = [
    re.compile(r"""(?:import|export)\b[^;'"]*?\bfrom\s*['"]([^'"]+)['"]"""),  # from-clause
    re.compile(r"""\bimport\s*['"]([^'"]+)['"]"""),                            # side-effect import
    re.compile(r"""\b(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)"""),      # require / dynamic import
]

specifiers = set()
for root, dirs, files in os.walk(scan_dir):
    dirs[:] = [d for d in dirs if d not in PRUNE]
    for fn in files:
        if not fn.endswith(EXTS):
            continue
        try:
            with open(os.path.join(root, fn), encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for rex in SPEC_RES:
            for m in rex.findall(text):
                # Ignore relative / absolute local imports — only bare packages.
                if m and not m.startswith((".", "/")):
                    specifiers.add(m)

installed = set()
pkg = _read_json(impl_pkg)
if isinstance(pkg, dict):
    installed.update((pkg.get("dependencies") or {}).keys())
    installed.update((pkg.get("devDependencies") or {}).keys())


def _spec_matches(candidate):
    for spec in specifiers:
        if spec == candidate or spec.startswith(candidate + "/"):
            return True
    return False


imported_names = []
unused = []
for lib in detected_libs:
    if any(_spec_matches(c) for c in lib["anyOf"]):
        imported_names.append(lib["name"])
    else:
        is_installed = any(c in installed for c in lib["anyOf"])
        unused.append({
            "name": lib["name"],
            "anyOf": lib["anyOf"],
            "installed": is_installed,
        })

if not unused:
    _write("pass",
           f"all {len(detected_libs)} detected animation library(ies) imported in impl source",
           imported=imported_names)
    sys.exit(0)

parts = []
for u in unused:
    state = "installed but never imported" if u["installed"] else "neither installed nor imported"
    parts.append(f"{u['name']} ({state}; import one of: {', '.join(u['anyOf'])})")
_write("fail",
       "ref animation library declared but not imported in impl source (rAF-shim "
       "loophole) — " + "; ".join(parts),
       imported=imported_names, unused=unused)
sys.exit(0)
PY

STATUS=$(python3 -c "import json; print(json.load(open('$OUT'))['status'])")

case "$STATUS" in
  pass)
    echo "✓ library-usage: PASS"
    exit 0
    ;;
  skip)
    echo "▸ library-usage: SKIP"
    exit 0
    ;;
  fail)
    REASON=$(python3 -c "import json,sys; print(json.load(open('$OUT')).get('reason',''))" 2>/dev/null || true)
    echo "✗ library-usage: FAIL — $REASON" >&2
    exit 1
    ;;
  *)
    echo "library-usage: unexpected status '$STATUS'" >&2
    exit 2
    ;;
esac
