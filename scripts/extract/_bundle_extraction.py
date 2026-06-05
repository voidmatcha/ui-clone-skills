"""Deterministic JS-bundle parser for animation/scroll library parameters.

Extracted from `scripts/extract/bundle-extraction.sh` (HANDOVER.md Item 2)
so the parsing logic is unit-testable. The shell wrapper handles
input/output paths and skip-on-missing-bundles; this module is the
parser.

Public entry point:
    main(argv) -> int    # argv = [ref_dir, out_path]; returns exit code

Importable helpers:
    parse_bundles(ref_dir: Path) -> dict
        Reads all .js files under `ref_dir/bundles/`, returns the
        extraction plan as a dict (same shape that gets written to
        bundle-extraction.json).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def detect_in_text(text: str, marker: str) -> bool:
    """Case-insensitive substring match. Cheap pre-check before regex."""
    return marker.lower() in text.lower()


def _build_text_index(bundles_dir: Path, ref_dir: Path) -> tuple[str, list[tuple[str, int]], list[str]]:
    """Read all .js files under bundles_dir, return (all_text, file_offsets, parts).

    `file_offsets` is a list of (relative_filename, byte_offset_in_concat)
    used by find_file_for_offset to attribute regex matches to their
    source bundle.
    """
    js_files = sorted(bundles_dir.rglob("*.js"))
    all_text_parts: list[str] = []
    file_offsets: list[tuple[str, int]] = []
    offset = 0
    for jf in js_files:
        try:
            t = jf.read_text(errors="ignore")
        except OSError:
            continue
        file_offsets.append((str(jf.relative_to(ref_dir)), offset))
        all_text_parts.append(t)
        offset += len(t)
    all_text = "\n".join(all_text_parts)
    return all_text, file_offsets, all_text_parts


def _find_file_for_offset(file_offsets: list[tuple[str, int]], off: int) -> str:
    """Return the relative filename whose concat-offset bracket contains `off`."""
    fname = file_offsets[0][0] if file_offsets else "?"
    for f, o in file_offsets:
        if o <= off:
            fname = f
    return fname


def _extract_lenis(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find `new Lenis({...})` constructor sites and parse their options."""
    extracts: list[dict] = []
    if not (detect_in_text(all_text, "new Lenis(") or detect_in_text(all_text, "lerp:")):
        return extracts
    for m in re.finditer(r"new\s+Lenis\s*\(\s*(\{[^{}]{0,500}\})", all_text):
        opts_raw = m.group(1)
        opts: dict = {}
        for key in ("lerp", "duration", "smoothWheel", "smoothTouch", "touchMultiplier", "direction", "easing"):
            km = re.search(rf"{key}\s*:\s*([^,}}\n]+)", opts_raw)
            if km:
                opts[key] = km.group(1).strip()
        extracts.append({
            "source": _find_file_for_offset(file_offsets, m.start()),
            "options": opts,
            "confidence": "high" if opts else "low",
        })
    return extracts


def _extract_gsap(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find GSAP timeline/tween/ScrollTrigger construction sites."""
    calls: list[dict] = []
    for pattern, kind in [
        (r"gsap\.timeline\s*\(\s*(\{[^{}]{0,300}\})?", "timeline"),
        (r"gsap\.(?:to|from|fromTo)\s*\(\s*([^,]+)\s*,\s*(\{[^{}]{0,500}\})", "tween"),
        (r"ScrollTrigger\.create\s*\(\s*(\{[^{}]{0,500}\})", "scrollTrigger"),
    ]:
        for m in re.finditer(pattern, all_text):
            calls.append({
                "kind": kind,
                "source": _find_file_for_offset(file_offsets, m.start()),
                "raw": m.group(0)[:200],
                "confidence": "medium",  # minified args hard to fully parse
            })
    return calls


_SCRUB_PROPS = (
    r"scale[XYZ]?|rotate[XYZ]?|opacity|x|y|skew[XY]|filter|backgroundColor"
)


def _resolve_scrub_property(result_var: str | None, window: str) -> str | None:
    """Resolve which motion property a useTransform result drives.

    `result_var` is the LHS the transform was assigned to (e.g. ``E`` in
    ``E=(0,s.G)(p,[...],[...])``). The property binding appears later in the
    component as ``{scale:E}`` / ``style:{opacity:E}``. Framer sites often wrap
    the transform in a useSpring before binding (``S=(0,l.z)(E,{stiffness})`` ->
    ``{scale:S}``), so we follow one spring/derive hop. Returns the property name
    (scale/opacity/y/...) or None when it cannot be resolved.
    """
    if not result_var:
        return None
    direct = re.search(
        r"(" + _SCRUB_PROPS + r")\s*:\s*" + re.escape(result_var) + r"\b", window
    )
    if direct:
        return direct.group(1)
    # one hop: SPRING = (0,NS)(result_var, { ... }) ; then {prop: SPRING}
    hop = re.search(
        r"(\w+)\s*=\s*\(0,[\w$.]+\)\(\s*" + re.escape(result_var) + r"\s*,\s*\{",
        window,
    )
    if hop:
        spring_var = hop.group(1)
        hopped = re.search(
            r"(" + _SCRUB_PROPS + r")\s*:\s*" + re.escape(spring_var) + r"\b", window
        )
        if hopped:
            return hopped.group(1)
    return None


def _extract_framer_motion(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find Framer Motion scroll hooks, including in minified bundles.

    Minification mangles the hook identifiers (``useScroll`` -> ``(0,o.L)``,
    ``useTransform`` -> ``(0,s.G)``, ``useMotionValueEvent`` -> ``(0,$.L)``),
    so the literal-name patterns below match nothing on a real production
    build. We therefore ALSO anchor on Framer's stable API string literals
    that survive minification:

      * ``useScroll``: ``{scrollYProgress:VAR}=(0,NS)({target:T,offset:[...]})``
      * ``useTransform`` bound to that progress var: ``(0,NS)(VAR,[in],[out])``
      * ``useMotionValueEvent`` threshold: ``(0,NS)(VAR,"change",cb)``

    Keying on the stable literals (not the per-build mangled function names)
    keeps the extractor general across sites. The ``transforms`` search is
    windowed to ~2.5 KB after each useScroll site to keep a single-letter
    progress var local to its own component (minified vars are reused).
    """
    uses: list[dict] = []

    # --- A) Minified scroll-scrub: stable Framer API literals -------------
    scroll_re = re.compile(
        r"\{\s*scrollYProgress\s*:\s*(\w+)\s*\}\s*=\s*"
        r"\(0,[\w$.]+\)\(\s*(\{[^{}]{0,200}\})\s*\)"
    )
    for m in scroll_re.finditer(all_text):
        progress_var = m.group(1)
        opts = m.group(2)
        tgt = re.search(r"target\s*:\s*(\w+)", opts)
        off = re.search(r"offset\s*:\s*(\[[^\]]{0,120}\])", opts)
        window = all_text[m.start(): m.start() + 2500]
        # The bound property (scale vs opacity vs y) is what makes a scrub
        # reproducible — a scale band and an opacity band render differently.
        # Resolve it from the transform's result var, allowing one useSpring
        # hop (out=useTransform(...); spring=useSpring(out); style={scale:spring}).
        prop_window = all_text[m.start(): m.start() + 4000]
        # Input range may be a plain bracket OR a media-query ternary
        # (cond?[...]:[...]); output is always a bracket. Capture the optional
        # result-var LHS so we can resolve the bound property.
        tf_re = re.compile(
            r"(?:(\w+)\s*=\s*)?\(0,[\w$.]+\)\(\s*" + re.escape(progress_var) +
            r"\s*,\s*(\[[^\]]{0,160}\]|[\w$]{1,3}\?\[[^\]]{0,90}\]:\[[^\]]{0,90}\])"
            r"\s*,\s*(\[[^\]]{0,200}\])\s*\)"
        )
        transforms = [
            {
                "input": t.group(2),
                "output": t.group(3),
                "property": _resolve_scrub_property(t.group(1), prop_window),
            }
            for t in tf_re.finditer(window)
        ]
        uses.append({
            "kind": "useScroll",
            "progressVar": progress_var,
            "target": tgt.group(1) if tgt else None,
            "offset": off.group(1) if off else None,
            "transforms": transforms[:12],
            "transformCount": len(transforms),
            "source": _find_file_for_offset(file_offsets, m.start()),
            "confidence": "high",
            "minified": True,
        })

    # useMotionValueEvent threshold callbacks drive per-word/line scroll
    # highlights; the `(0,NS)(` interop prefix distinguishes these from a
    # plain `el.addEventListener("change", ...)`.
    for m in re.finditer(r"\(0,[\w$.]+\)\(\s*(\w+)\s*,\s*[\"']change[\"']\s*,", all_text):
        uses.append({
            "kind": "useMotionValueEvent",
            "valueVar": m.group(1),
            "event": "change",
            "source": _find_file_for_offset(file_offsets, m.start()),
            "confidence": "medium",
            "minified": True,
        })

    # --- B) Unminified fallback: literal hook names ----------------------
    for pattern, kind in [
        (r"\buseScroll\s*\(\s*(\{[^{}]{0,200}\})?", "useScroll"),
        (r"\buseTransform\s*\(\s*[^,]+,\s*(\[[^\]]+\])\s*,\s*(\[[^\]]+\])", "useTransform"),
        (r"\buseInView\s*\(\s*[^,]+,\s*(\{[^{}]{0,200}\})", "useInView"),
    ]:
        for m in re.finditer(pattern, all_text):
            uses.append({
                "kind": kind,
                "source": _find_file_for_offset(file_offsets, m.start()),
                "raw": m.group(0)[:200],
                "confidence": "medium",
            })

    return uses


def _extract_anime_js(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find anime() construction sites."""
    calls: list[dict] = []
    for m in re.finditer(r"anime\s*\(\s*(\{[^{}]{0,500}\})", all_text):
        calls.append({
            "source": _find_file_for_offset(file_offsets, m.start()),
            "raw": m.group(0)[:200],
            "confidence": "medium",
        })
    return calls


def _extract_webflow_ix2(all_text: str, file_offsets: list[tuple[str, int]]) -> dict | None:
    """Find Webflow IX2 actionTypeId markers. Returns dict or None when absent."""
    if not (detect_in_text(all_text, "actionTypeId") or detect_in_text(all_text, "ix2")):
        return None
    actions: list[dict] = []
    for m in re.finditer(r"actionTypeId\s*:\s*['\"]([^'\"]+)['\"]", all_text):
        actions.append({
            "actionType": m.group(1),
            "source": _find_file_for_offset(file_offsets, m.start()),
            "confidence": "high",  # actionTypeId is a clear marker
        })
    if not actions:
        return None
    return {
        "actions": actions[:50],  # cap to avoid huge output
        "totalActions": len(actions),
    }


def parse_bundles(ref_dir: Path) -> dict:
    """Parse all .js files under `ref_dir/bundles/` and return the extraction plan.

    Returns the same dict shape that gets serialised to
    `<ref_dir>/bundle-extraction.json` by the shell wrapper.
    """
    bundles_dir = ref_dir / "bundles"
    if not bundles_dir.is_dir():
        return {
            "schemaVersion": 1,
            "bundlesScanned": 0,
            "totalSizeKB": 0,
            "extractions": {},
            "unresolved": [],
        }

    all_text, file_offsets, parts = _build_text_index(bundles_dir, ref_dir)
    js_count = len(file_offsets)
    total_size_kb = sum(len(p) for p in parts) // 1024

    extractions: dict = {}
    lenis = _extract_lenis(all_text, file_offsets)
    if lenis:
        extractions["lenis"] = lenis
    gsap = _extract_gsap(all_text, file_offsets)
    if gsap:
        extractions["gsap"] = gsap
    fm = _extract_framer_motion(all_text, file_offsets)
    if fm:
        extractions["framerMotion"] = fm
    anime = _extract_anime_js(all_text, file_offsets)
    if anime:
        extractions["animeJs"] = anime
    ix2 = _extract_webflow_ix2(all_text, file_offsets)
    if ix2 is not None:
        extractions["webflowIX2"] = ix2

    return {
        "schemaVersion": 1,
        "bundlesScanned": js_count,
        "totalSizeKB": total_size_kb,
        "extractions": extractions,
        "unresolved": [],
    }


def main(argv: list[str]) -> int:
    """CLI entry point. argv = [ref_dir, out_path]."""
    if len(argv) < 2:
        print("Usage: _bundle_extraction.py <ref-dir> <out-path>", file=sys.stderr)
        return 2
    ref_dir = Path(argv[0])
    out_path = Path(argv[1])

    plan = parse_bundles(ref_dir)
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")

    js_count = plan["bundlesScanned"]
    total_size_kb = plan["totalSizeKB"]
    extractions = plan["extractions"]
    print(f"✓ bundle-extraction.json written → {out_path}")
    print(f"  bundles scanned: {js_count} ({total_size_kb} KB)")
    for lib in sorted(extractions.keys()):
        count = (
            len(extractions[lib])
            if isinstance(extractions[lib], list)
            else extractions[lib].get("totalActions", "?")
        )
        print(f"  {lib}: {count} extractions")
    if not extractions:
        print("  no library construction sites detected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
