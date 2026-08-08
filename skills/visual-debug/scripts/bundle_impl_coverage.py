#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys

out_path, status, reason, bundle_map_path, impl_pkg = sys.argv[1:6]

# Signature → list of acceptable npm package names (any one satisfies)
SIG_TO_PKG = {
    "gsap-like-strings": ["gsap", "@gsap/business", "@gsap/shockingly"],
    "motion-like": ["framer-motion", "motion", "@motionone/dom", "@motionone/react"],
    "lenis": ["lenis", "@studio-freight/lenis"],
    "react-spring": ["@react-spring/web", "react-spring"],
    "tween-like": ["@tweenjs/tween.js"],
    "popmotion-like": ["popmotion"],
    "three-like": ["three", "@react-three/fiber", "@react-three/drei"],
}

detected_libs = []
notes_libs = []

# Mapping for the newer `libraries: {gsap: bool, ...}` schema to the
# signature names used in the older `chunks[].libs[]` schema. Both shapes
# coexist in the field — bundle-map.sh varies by site.
LIBRARIES_TO_SIG = {
    "gsap": "gsap-like-strings",
    "scrollTrigger": "gsap-like-strings",
    "framerMotion": "motion-like",
    "motion": "motion-like",
    "lenis": "lenis",
    "three": "three-like",
    "tween": "tween-like",
    "popmotion": "popmotion-like",
    "reactSpring": "react-spring",
}

if os.path.exists(bundle_map_path):
    try:
        with open(bundle_map_path) as fh:
            bm = json.load(fh)
        # Shape A: `chunks: {name: {role, libs[]}}` (older nested dict)
        chunks_val = bm.get("chunks")
        if isinstance(chunks_val, dict):
            for ch in chunks_val.values():
                if not isinstance(ch, dict):
                    continue
                for lib in ch.get("libs") or []:
                    if isinstance(lib, str):
                        detected_libs.append(lib)
        # Shape B: `libraries: {gsap: bool, lenis: bool, ...}` (newer flat)
        libraries_val = bm.get("libraries")
        if isinstance(libraries_val, dict):
            for key, present in libraries_val.items():
                if present and key in LIBRARIES_TO_SIG:
                    detected_libs.append(LIBRARIES_TO_SIG[key])
        # `evidence: {lib: [chunks]}` doubles as confirmation in newer shape
        evidence_val = bm.get("evidence")
        if isinstance(evidence_val, dict):
            for key, chunks_for_lib in evidence_val.items():
                if not chunks_for_lib:
                    continue
                if key in LIBRARIES_TO_SIG:
                    detected_libs.append(LIBRARIES_TO_SIG[key])
        notes = bm.get("notes") or ""
        if isinstance(notes, str):
            # Lenis is often noted in `notes` rather than chunks[].libs[].
            if re.search(r"\blenis\b", notes, re.IGNORECASE):
                notes_libs.append("lenis")
    except Exception:
        pass

all_libs = list(dict.fromkeys(detected_libs + notes_libs))  # preserve order, dedup

installed: set[str] = set()
if impl_pkg and os.path.exists(impl_pkg):
    try:
        with open(impl_pkg) as fh:
            pkg = json.load(fh)
        installed.update((pkg.get("dependencies") or {}).keys())
        installed.update((pkg.get("devDependencies") or {}).keys())
    except Exception:
        pass

missing = []
for lib in all_libs:
    candidates = SIG_TO_PKG.get(lib)
    if not candidates:
        # Unknown signature — skip; we only enforce ones we can map.
        continue
    if not any(c in installed for c in candidates):
        missing.append({"signature": lib, "anyOf": candidates})

with open(out_path, "w") as fh:
    json.dump(
        {
            "schemaVersion": 1,
            "status": status,
            "detectedLibs": all_libs,
            "missingDeps": missing,
            "implPkgJson": impl_pkg,
            "reason": reason,
        },
        fh,
        indent=2,
    )
