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


# Object arguments in real bundles nest: `gsap.to(e,{y:-100,scrollTrigger:{...}})`
# is the canonical scroll-driven form. A `\{[^{}]*\}` pattern cannot express that
# — the character class excludes the very braces the nested config needs — so
# those call sites were skipped entirely and the spec came out under-populated.
# Scan for the balanced closing brace instead, stepping over string literals so
# a `{` or `}` inside a quoted value does not unbalance the count.
_MAX_OBJECT_SPAN = 20000


def _balanced_object(text: str, open_index: int, limit: int = _MAX_OBJECT_SPAN) -> str | None:
    """Return the `{...}` literal starting at `open_index`, or None.

    None when the text does not start with `{`, the braces never balance, or the
    literal runs past `limit` (a runaway match on a minified megabundle).
    """
    if open_index >= len(text) or text[open_index] != "{":
        return None
    depth = 0
    index = open_index
    end = min(len(text), open_index + limit)
    quote: str | None = None
    while index < end:
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_index:index + 1]
        index += 1
    return None


def _object_after(text: str, index: int) -> tuple[str | None, int]:
    """Skip whitespace from `index` and read a balanced object literal.

    Returns (literal, index just past it); (None, index) when the next
    non-space character does not open an object.
    """
    while index < len(text) and text[index].isspace():
        index += 1
    literal = _balanced_object(text, index)
    if literal is None:
        return None, index
    return literal, index + len(literal)


def _extract_lenis(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find `new Lenis({...})` constructor sites and parse their options."""
    extracts: list[dict] = []
    if not (detect_in_text(all_text, "new Lenis(") or detect_in_text(all_text, "lerp:")):
        return extracts
    for m in re.finditer(r"new\s+Lenis\s*\(", all_text):
        opts_raw, _ = _object_after(all_text, m.end())
        if opts_raw is None:
            continue
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


# Bundlers rename the imported `gsap` binding, so production call sites read
# `o.timeline({...})` / `r.to(el,{...})`, not `gsap.to(...)`. Requiring the
# literal prefix finds only the library's own internals (`gsap.registerPlugin`,
# `gsap.scaleX`) and misses every application tween. Match any short identifier
# and confirm the call is GSAP by the option keys its config carries — those
# keys are API names and survive minification, unlike the binding.
_GSAP_TWEEN_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.(to|from|fromTo)\s*\(")
_GSAP_TIMELINE_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.timeline\s*\(")
_SCROLLTRIGGER_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.create\s*\(")

# Option names that identify a GSAP config. Deliberately API-specific: a generic
# `.to()` on an unrelated object will not carry these.
_GSAP_CONFIG_KEYS = (
    "scrollTrigger", "ease", "duration", "stagger", "repeat", "yoyo",
    "autoAlpha", "keyframes", "motionPath", "overwrite", "transformOrigin",
    "onUpdate", "onComplete", "onStart", "scrub", "delay",
)
# A ScrollTrigger.create config always describes a scroll range.
_SCROLLTRIGGER_CONFIG_KEYS = ("trigger", "start", "end", "scrub", "pin", "onEnter")


def _looks_like_gsap_config(configs: list[str], keys: tuple[str, ...]) -> bool:
    """True when any config object declares one of `keys` as a property."""
    for config in configs:
        for key in keys:
            if re.search(rf"[{{,\s]{key}\s*:", config):
                return True
    return False


def _gsap_call_entry(
    kind: str,
    match_start: int,
    file_offsets: list[tuple[str, int]],
    configs: list[str],
    raw: str,
) -> dict:
    """One construction-site row, tagged with the config text actually read."""
    joined = " ".join(configs)
    source = _find_file_for_offset(file_offsets, match_start)
    # Stable id so transition-spec.json can cite a specific construction site,
    # the same way it cites animation-runtime-dump.json scrollLinkedStyles rows.
    # Keyed on file + byte offset: re-running the parser over the same bundles
    # reproduces it, and two calls in one file never collide.
    source_id = f"gsap:{Path(source).name}:{match_start}"
    return {
        "sourceId": source_id,
        "kind": kind,
        "source": source,
        "raw": raw[:200],
        # The full config is what Phase 5d maps to transitions[]; `raw` stays
        # truncated for readability but must not be the only record, or a
        # nested scrollTrigger falls off the end of the 200-char window.
        "config": [c[:2000] for c in configs],
        "scrollLinked": "scrollTrigger" in joined or "ScrollTrigger" in joined,
        # A site whose object argument parsed is worth more than a bare name
        # match: `gsap.timeline()` with no config carries no parameters at all.
        "confidence": "medium" if configs else "low",
    }


def _extract_gsap(all_text: str, file_offsets: list[tuple[str, int]]) -> list[dict]:
    """Find GSAP timeline/tween/ScrollTrigger construction sites.

    Object arguments are read with a balanced-brace scan, so the nested
    `scrollTrigger: {...}` config that defines scroll-driven motion is captured
    rather than skipped.
    """
    calls: list[dict] = []

    for m in _GSAP_TWEEN_RE.finditer(all_text):
        binding, verb = m.group(1), m.group(2)
        cursor = m.end()
        # Target argument: everything up to the comma that precedes the first
        # object. Bounded so a malformed site cannot scan the whole bundle.
        comma = all_text.find(",", cursor, cursor + 400)
        if comma == -1:
            continue
        configs: list[str] = []
        first, after_first = _object_after(all_text, comma + 1)
        if first is not None:
            configs.append(first)
            if verb == "fromTo":
                # fromTo(target, fromVars, toVars) — the TO object is the one
                # that carries scrollTrigger, so reading only the first
                # argument misses the scroll linkage entirely.
                comma2 = all_text.find(",", after_first, after_first + 40)
                if comma2 != -1:
                    second, _ = _object_after(all_text, comma2 + 1)
                    if second is not None:
                        configs.append(second)
        if binding != "gsap" and not _looks_like_gsap_config(configs, _GSAP_CONFIG_KEYS):
            continue
        entry = _gsap_call_entry("tween", m.start(), file_offsets, configs, all_text[m.start():m.end() + 200])
        entry["binding"] = binding
        calls.append(entry)

    for m in _GSAP_TIMELINE_RE.finditer(all_text):
        binding = m.group(1)
        config, _ = _object_after(all_text, m.end())
        configs = [config] if config is not None else []
        # A bare `x.timeline()` carries no evidence it is GSAP at all; only the
        # literal binding is trusted without config keys.
        if binding != "gsap" and not _looks_like_gsap_config(configs, _GSAP_CONFIG_KEYS):
            continue
        entry = _gsap_call_entry("timeline", m.start(), file_offsets, configs, all_text[m.start():m.end() + 200])
        entry["binding"] = binding
        calls.append(entry)

    for m in _SCROLLTRIGGER_RE.finditer(all_text):
        binding = m.group(1)
        config, _ = _object_after(all_text, m.end())
        configs = [config] if config is not None else []
        if binding != "ScrollTrigger" and not _looks_like_gsap_config(
            configs, _SCROLLTRIGGER_CONFIG_KEYS
        ):
            continue
        entry = _gsap_call_entry("scrollTrigger", m.start(), file_offsets, configs, all_text[m.start():m.end() + 200])
        entry["scrollLinked"] = True
        entry["binding"] = binding
        calls.append(entry)

    return calls


_SCRUB_PROPS = (
    r"scale[XYZ]?|rotate[XYZ]?|opacity|x|y|skew[XY]|filter|"
    r"backgroundColor|width|height|borderRadius"
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
    """Find anime() construction sites and read their config object.

    Keyframe values are arrays of objects (`translateY:[{value:0},{value:100}]`),
    so the config has to be read with a balanced scan rather than a flat
    brace pattern.
    """
    calls: list[dict] = []
    for m in re.finditer(r"\banime\s*\(", all_text):
        config, _ = _object_after(all_text, m.end())
        if config is None:
            # No object argument — `anime(` here is a call through a variable or
            # an unrelated identifier, not a construction site with parameters.
            continue
        calls.append({
            "source": _find_file_for_offset(file_offsets, m.start()),
            "raw": all_text[m.start():m.end() + 200][:200],
            "config": config[:2000],
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


_HOVER_SIZE_RE = re.compile(
    r"className:\s*(?:[\w$]+\(\)\.)?(\w+)\s*,[^{}]{0,120}?"
    r"initial:\{(width|maxWidth):0\}\s*,\s*animate:\{(?:width|maxWidth):([^{}]+)\}"
    r"(?:\s*,\s*transition:\{([^{}]*)\})?"
)


def _extract_hover_size_expansions(
    all_text: str, file_offsets: list[tuple[str, int]]
) -> list[dict]:
    """Size-expansion components: initial width/maxWidth 0 animating open on
    a state flag (nav pill labels, expanding menu chips). Observed failure
    mode: the expansion lived only in the JS bundle, never reached a spec
    entry, and the impl shipped the labels baked at width:0 with no hover
    behavior — unverified by every hover gate. Each match also resolves the
    CSS-module class token to its concrete class name via the bundle's
    class-map literal when present, so downstream spec entries get a real
    selector."""
    out: list[dict] = []
    for m in _HOVER_SIZE_RE.finditer(all_text):
        token, prop, to_expr, transition = (
            m.group(1), m.group(2), m.group(3), m.group(4) or ""
        )
        resolved = None
        rm = re.search(
            rf"{re.escape(token)}\s*:\s*\"([A-Za-z0-9_-]+)\"", all_text
        )
        if rm:
            resolved = rm.group(1)
        out.append(
            {
                "kind": "size-expansion",
                "classToken": token,
                "resolvedClassName": resolved,
                "property": prop,
                "from": "0",
                "to": to_expr.strip()[:80],
                "transition": transition.strip()[:120],
                "source": _find_file_for_offset(file_offsets, m.start()),
                "confidence": "high",
                "minified": True,
            }
        )
    return out


# Active-state (scroll/active-swap) width reveals: `initial:{width:0},
# animate:{width:<flag>?"auto"|<n>:0}`. The reveal is gated on a STATE flag
# (the active-section flag in a nav state machine), not a hover pointer event —
# so it is invisible to the hover gate. Loop-10/11 defect: scrolling swaps the
# nav active state but the newly-active button's label never reveals (container
# baked width:0). Unlike _HOVER_SIZE_RE this does not require the className to be
# adjacent (real minified state-machine code interleaves layout/style props), so
# it catches reveals the hover extractor misses; the nearest className is
# resolved separately.
_ACTIVE_STATE_SIZE_RE = re.compile(
    r"initial:\{(width|maxWidth):0\}\s*,\s*animate:\{(?:width|maxWidth):"
    r"\s*([A-Za-z_$][\w$]*)\s*\?\s*(\"auto\"|'auto'|\d+)\s*:\s*0\s*\}"
    r"(?:\s*,\s*transition:\{([^{}]*)\})?"
)
_CLASSNAME_BEFORE_RE = re.compile(
    r"className:\s*(?:(?:[\w$]+\(\)|[\w$]+(?:\.[\w$]+)+)\.)?(\w+)"
)


def _extract_active_state_expansions(
    all_text: str, file_offsets: list[tuple[str, int]]
) -> list[dict]:
    """State-flag-driven width reveals (active-section label expansion). The
    expansion is gated on a state flag, so it fires on a state change (scroll to
    a section) rather than a hover — the hover gate cannot verify it. Each match
    resolves the nearest preceding className token to a concrete class via the
    bundle class-map when present."""
    out: list[dict] = []
    for m in _ACTIVE_STATE_SIZE_RE.finditer(all_text):
        prop, flag, to_expr, transition = (
            m.group(1), m.group(2), m.group(3), m.group(4) or ""
        )
        window = all_text[max(0, m.start() - 200) : m.start()]
        cn = list(_CLASSNAME_BEFORE_RE.finditer(window))
        token = cn[-1].group(1) if cn else None
        resolved = None
        if token:
            rm = re.search(rf"{re.escape(token)}\s*:\s*\"([A-Za-z0-9_-]+)\"", all_text)
            if rm:
                resolved = rm.group(1)
        out.append(
            {
                "kind": "active-state-expansion",
                "classToken": token,
                "resolvedClassName": resolved,
                "property": prop,
                "stateFlag": flag,
                "from": "0",
                "to": to_expr.strip("\"'")[:80],
                "transition": transition.strip()[:120],
                "source": _find_file_for_offset(file_offsets, m.start()),
                "confidence": "high" if token else "medium",
                "minified": True,
            }
        )
    return out


# Carousel/slider libraries whose construction config the regex parser
# deliberately does NOT extract: their parameters live in nested option objects
# with responsive breakpoint maps (slidesPerView/perPage per breakpoint, effect,
# autoplay, loop) that minified-brace regexes cannot parse reliably. We flag
# PRESENCE only so Step 5d can dispatch the bundle-analyzer LLM for exactly these
# gaps (script-first, dispatch-on-gap) instead of silently shipping a carousel
# with no runtime config. Markers are stable across vanilla + framework builds:
# the `new <Lib>(` constructor and the DOM class literals the library renders.
_UNRESOLVED_LIB_MARKERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "swiper",
        (r"new\s+Swiper\s*\(", r"swiper-slide", r"swiper-wrapper"),
        "Swiper carousel config (slidesPerView, breakpoints, effect, autoplay, "
        "loop) — needs bundle-analyzer LLM extraction",
    ),
    (
        "splide",
        (r"new\s+Splide\s*\(", r"splide__slide", r"splide__track"),
        "Splide carousel config (perPage, breakpoints, type, autoplay) — needs "
        "bundle-analyzer LLM extraction",
    ),
)


def _detect_unresolved_libraries(
    all_text: str, file_offsets: list[tuple[str, int]]
) -> list[dict]:
    """Flag carousel/slider libraries present in the bundle whose config the
    regex parser does not attempt, so Step 5d dispatches the bundle-analyzer LLM
    for just these gaps. Presence only — no config is guessed. One entry per
    library, attributed to the first matching marker's source bundle."""
    out: list[dict] = []
    for library, markers, reason in _UNRESOLVED_LIB_MARKERS:
        hit = None
        for marker in markers:
            m = re.search(marker, all_text)
            if m:
                hit = m
                break
        if hit is not None:
            out.append({
                "library": library,
                "reason": reason,
                "source": _find_file_for_offset(file_offsets, hit.start()),
            })
    framer_hit = re.search(
        r"scrollYProgress\b.*?(?:\buseScroll\b|framer-motion)|"
        r"(?:\buseScroll\b|framer-motion).*?scrollYProgress\b",
        all_text,
        re.DOTALL,
    )
    if framer_hit is not None and not _extract_framer_motion(all_text, file_offsets):
        out.append({
            "library": "framer-motion",
            "reason": (
                "Framer Motion scroll markers found but deterministic extraction "
                "resolved zero scroll sites — needs bundle-analyzer LLM extraction"
            ),
            "source": _find_file_for_offset(file_offsets, framer_hit.start()),
        })
    return out


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
    expansions = _extract_hover_size_expansions(all_text, file_offsets)
    if expansions:
        extractions["hoverSizeExpansions"] = expansions
    active_expansions = _extract_active_state_expansions(all_text, file_offsets)
    if active_expansions:
        extractions["activeStateExpansions"] = active_expansions

    return {
        "schemaVersion": 1,
        "bundlesScanned": js_count,
        "totalSizeKB": total_size_kb,
        "extractions": extractions,
        "unresolved": _detect_unresolved_libraries(all_text, file_offsets),
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
    for u in plan.get("unresolved") or []:
        print(
            f"  ⚠ unresolved: {u['library']} ({u['source']}) — "
            "dispatch bundle-analyzer per Step 5d"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
