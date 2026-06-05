#!/usr/bin/env bash
# scaffold-to-jsx.sh — deterministic transpiler: structure.json → JSX components.
#
# Reads <ref-dir>/structure.json (with Fix 13 per-node `styles` and Fix 6 v1
# `text` fields) and <ref-dir>/section-map.json, emits one component file per
# section into <impl-dir>/src/components/<Name>.tsx with verbatim text,
# verbatim inline styles, and tag-preserving JSX.
#
# Architectural intent: replace the LLM-interpretation step of Phase 4 with
# a deterministic AST-style transform. The LLM was the source of fidelity
# drift across V1–V9 (text fabrication, stub regression, wrong root tags,
# Tailwind class guessing). The transpiler removes that step entirely for
# layout/text/style; the LLM still gets a turn at the end for things the
# transpiler can't deduce (event handlers, state, animation triggers).
#
# Pattern: similar to Builder.io's Mitosis (universal AST → React/Vue/...)
# and Design2Code's text-augmented prompting (arXiv 2403.03163), but with
# the entire HTML→JSX pass moved out of the LLM.
#
# Usage:
#   scaffold-to-jsx.sh <ref-dir> <impl-dir> [--out-dir <impl/src/components>]
#
# Writes one .tsx per ref section. Idempotent — re-running overwrites.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REF_DIR=""
IMPL_DIR=""
OUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT_DIR="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then IMPL_DIR="$1"
      else echo "scaffold-to-jsx: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" || -z "$IMPL_DIR" ]]; then
  echo "usage: scaffold-to-jsx.sh <ref-dir> <impl-dir> [--out-dir <path>]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "scaffold-to-jsx: ref dir not found: $REF_DIR" >&2; exit 2
fi
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="$IMPL_DIR/src/components"
fi
mkdir -p "$OUT_DIR"

STRUCT="$REF_DIR/structure.json"
SECMAP="$REF_DIR/section-map.json"
if [[ ! -f "$STRUCT" ]]; then
  echo "scaffold-to-jsx: structure.json missing — run Phase 2 first" >&2; exit 2
fi

python3 - "$STRUCT" "$SECMAP" "$OUT_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Generality knobs — realfood-derived heuristic defaults, env-overridable
# per-site like the rest of the pipeline's thresholds (AE_THRESHOLD,
# UI_CLONE_GEOM_*). Defaults unchanged.
TRANSFORM_MIN_PX = _env_float("UI_CLONE_TRANSFORM_MIN_PX", 24.0)
SPLIT_TEXT_CHAR_RATIO = _env_float("UI_CLONE_SPLIT_TEXT_CHAR_RATIO", 0.85)
SPLIT_TEXT_MIN_LEAVES = int(_env_float("UI_CLONE_SPLIT_TEXT_MIN_LEAVES", 12))
# Fix 93 (B4) — reflow thresholds were hardcoded and mutually inconsistent
# (child 360, root 480). They legitimately differ (a child container reflows at
# a phone viewport; the whole-page root reflows at a wider breakpoint), so keep
# two env-tunable knobs with the original defaults rather than collapsing them.
REFLOW_CHILD_MIN_PX = _env_float("UI_CLONE_REFLOW_CHILD_MIN_PX", 360.0)
REFLOW_ROOT_MIN_PX = _env_float("UI_CLONE_REFLOW_ROOT_MIN_PX", 480.0)


# Whether the generation plan requires Lenis smooth scroll. Drives the
# <SmoothScroll> wrap in the page entry so the built page actually mounts the
# emitted helper (emit-scroll-helpers.sh writes src/lib/SmoothScroll.tsx).
SMOOTH_SCROLL_REQUIRED = False
SCROLL_DRIVEN_REQUIRED = False
# Fix 113 — JSX attrs for the deterministic scroll-zoom auto-wrap: the band of
# the first scrollScrub `scale` site, inlined so the wrapper needs no
# scrollScrubSites index. Empty string => no scale band => no auto-wrap.
SCRUB_WRAP_ATTRS = ""
try:
    _plan_p = Path(sys.argv[1]).parent / "generation-plan.json"
    if _plan_p.exists():
        _plan = json.loads(_plan_p.read_text())
        _ss = _plan.get("smoothScroll") if isinstance(_plan, dict) else None
        SMOOTH_SCROLL_REQUIRED = bool(isinstance(_ss, dict) and _ss.get("required"))
        _sd = _plan.get("scrollDriven") if isinstance(_plan, dict) else None
        SCROLL_DRIVEN_REQUIRED = bool(isinstance(_sd, dict) and _sd.get("required"))
        _scrub = _plan.get("scrollScrub") if isinstance(_plan, dict) else None
        if isinstance(_scrub, dict) and _scrub.get("required"):
            def _scrub_range(s):
                if not isinstance(s, str):
                    return None
                mm = re.search(r"\[([^\[\]]*)\]", s)  # ternary -> first bracket
                if not mm:
                    return None
                out = []
                for tok in mm.group(1).split(","):
                    tok = tok.strip()
                    if not tok:
                        continue
                    try:
                        out.append(float(tok))
                    except ValueError:
                        return None
                return out if len(out) >= 2 else None
            for _site in _scrub.get("sites", []) or []:
                if not isinstance(_site, dict):
                    continue
                _st = next((t for t in (_site.get("transforms") or [])
                            if isinstance(t, dict)
                            and (t.get("property") or "").startswith("scale")), None)
                if not _st:
                    continue
                _inp = _scrub_range(_st.get("input"))
                _outp = _scrub_range(_st.get("output"))
                if not _inp or not _outp or len(_inp) != len(_outp):
                    continue
                if not all(0 <= v <= 8 for v in _outp):  # plausibility
                    continue
                _off = None
                if isinstance(_site.get("offset"), str):
                    try:
                        _p = json.loads(_site["offset"])
                        if isinstance(_p, list) and len(_p) == 2:
                            _off = _p
                    except (json.JSONDecodeError, ValueError):
                        _off = None
                _a = f"scale={{{json.dumps([_inp, _outp])}}}"
                if _off is not None:
                    _a += f" offset={{{json.dumps(_off)}}}"
                SCRUB_WRAP_ATTRS = _a + " spring"
                break
except (OSError, json.JSONDecodeError):
    SMOOTH_SCROLL_REQUIRED = False
    SCROLL_DRIVEN_REQUIRED = False
    SCRUB_WRAP_ATTRS = ""

# P7 — video playback props (autoplay/loop/muted) per video, keyed by basename,
# from assets.json. A bare <video src> never plays; autoplay background videos
# need autoPlay/muted/loop/playsInline emitted as JS-runtime behavior.
VIDEO_PROPS = {}
try:
    _assets_p = Path(sys.argv[1]).parent / "assets.json"
    if _assets_p.exists():
        _assets = json.loads(_assets_p.read_text())
        for _v in (_assets.get("videos") or []) if isinstance(_assets, dict) else []:
            if not isinstance(_v, dict):
                continue
            _vsrc = _v.get("src") or ""
            _bn = _vsrc.split("?", 1)[0].rstrip("/").split("/")[-1]
            if _bn:
                VIDEO_PROPS[_bn] = {
                    "autoplay": bool(_v.get("autoplay")),
                    "loop": bool(_v.get("loop")),
                    "muted": bool(_v.get("muted")),
                }
except (OSError, json.JSONDecodeError):
    VIDEO_PROPS = {}

# P7 — final text of JS-injected elements (count-up stat numbers etc.) that are
# EMPTY in the static capture. runtime-text.json maps a class token to the list
# of final values in document order; injected into matching empty elements so
# the clone shows "50%/75%/90%" instead of blank bars. Producer: a runtime
# capture (scroll page, let animations finish, dump per-class final innerText).
RUNTIME_TEXT = {}
RUNTIME_TEXT_IDX = {}
try:
    _rt_p = Path(sys.argv[1]).parent / "runtime-text.json"
    if _rt_p.exists():
        _rt = json.loads(_rt_p.read_text())
        _by = _rt.get("byClass") if isinstance(_rt, dict) else None
        if isinstance(_by, dict):
            RUNTIME_TEXT = {k: list(v) for k, v in _by.items() if isinstance(v, list)}
except (OSError, json.JSONDecodeError):
    RUNTIME_TEXT = {}


# Tags whose elements are void in HTML (self-closing in JSX).
VOID_TAGS = {
    "area","base","br","col","embed","hr","img","input","link",
    "meta","param","source","track","wbr",
}
# Tags that don't render content — skip in JSX entirely.
SKIP_TAGS = {"script","style","link","meta","noscript","template"}
# Fix 20/21 — capture-time computed values frozen as inline constants break when
# the cloned impl reflows differently. On content-bearing elements convert a
# frozen px `height` into a `min-height` floor (and drop `max-height`) so the
# impl text/content can grow without clipping while a full-bleed section keeps
# its intended height; and reset `transform`/`opacity` that were captured
# mid-animation (scroll-reveal / parallax / stagger) back to rest.
REPLACED_TAGS = {"img","video","canvas","svg","iframe","picture","image",
                 "use","source","object","embed"}
# HTML→JSX attribute renames.
ATTR_RENAMES = {"class": "className", "for": "htmlFor"}

# SVG tags whose presence triggers the kebab→camelCase SVG attr map.
SVG_TAGS = {
    "svg","g","defs","use","symbol","marker","clippath","clip-path",
    "mask","pattern","filter","feblend","fecolormatrix","fecomposite",
    "fegaussianblur","femerge","femergenode","feoffset","feflood","fetile",
    "feturbulence","fedropshadow","fediffuselighting","fespecularlighting",
    "femorphology","feimage","fedisplacementmap",
    "lineargradient","radialgradient","stop",
    "path","rect","circle","ellipse","line","polyline","polygon",
    "text","textpath","tspan","title","desc","foreignobject",
}
# Subset of SVG attrs that need kebab→camelCase remap for JSX. Tags
# in SVG_TAGS get this mapping applied before emit.
SVG_ATTR_RENAMES = {
    "viewBox": "viewBox", "preserveAspectRatio": "preserveAspectRatio",
    "stroke-width": "strokeWidth",
    "stroke-linecap": "strokeLinecap",
    "stroke-linejoin": "strokeLinejoin",
    "stroke-miterlimit": "strokeMiterlimit",
    "stroke-dasharray": "strokeDasharray",
    "stroke-dashoffset": "strokeDashoffset",
    "stroke-opacity": "strokeOpacity",
    "fill-rule": "fillRule",
    "fill-opacity": "fillOpacity",
    "clip-rule": "clipRule",
    "clip-path": "clipPath",
    "stop-color": "stopColor",
    "stop-opacity": "stopOpacity",
    "gradientTransform": "gradientTransform",
    "gradientUnits": "gradientUnits",
    "spreadMethod": "spreadMethod",
    "xlink:href": "xlinkHref",
    "xlink:title": "xlinkTitle",
    "patternUnits": "patternUnits",
    "patternContentUnits": "patternContentUnits",
    "patternTransform": "patternTransform",
    "markerUnits": "markerUnits",
    "refX": "refX", "refY": "refY",
    "flood-color": "floodColor",
    "flood-opacity": "floodOpacity",
    "stdDeviation": "stdDeviation",
}
# SVG geometry/styling attrs (no rename needed but pass through to JSX).
SVG_PASSTHROUGH_ATTRS = {
    "id","xmlns","fill","stroke","opacity","mask","filter",
    "d","points","x","y","x1","y1","x2","y2","cx","cy","r","rx","ry",
    "width","height","transform","offset",
    "href","in","in2","result","values","operator","mode","type",
    "orient","overflow",
}


def kebab_to_camel(s):
    parts = s.split("-")
    return parts[0] + "".join(p.title() for p in parts[1:])


def rewrite_css_urls(value):
    """Replace every `url(...)` URL inside a CSS value with its
    locally-rewritten equivalent. Audit gap #3 — fixes the
    pseudo/background-image SVG leak where extract-dom captured a
    `background-image: url("https://cdn/.../ic.svg")` and
    scaffold-to-jsx emitted it verbatim. After this, the JSX style
    literal references /images/ic.svg matching the locally-downloaded
    file. Handles double-quote, single-quote, and unquoted URL forms.
    """
    if not isinstance(value, str) or "url(" not in value:
        return value

    def _replace(m):
        url = m.group("url")
        local = rewrite_asset_url(url)
        return f'url("{local}")'

    return re.sub(
        r'url\(\s*["\']?(?P<url>[^"\')]+?)["\']?\s*\)',
        _replace,
        value,
    )


def style_to_jsx(styles):
    """Convert a dict of CSS prop → string into a JSX-style object literal."""
    if not styles:
        return ""
    items = []
    # Properties whose values can hold url(...) tokens that need
    # rewriting to the locally-downloaded asset paths.
    URL_BEARING = {
        "background", "background-image", "mask", "mask-image",
        "border-image", "border-image-source",
        "list-style", "list-style-image",
        "cursor", "content", "src",
        "clip-path", "filter",
    }
    for k, v in styles.items():
        if k in URL_BEARING:
            v = rewrite_css_urls(v)
        if k in ("height", "min-height"):
            v = _vh_or_px(v)  # Fix 80 — authored-vh tracks back to vh
        ck = kebab_to_camel(k)
        # Escape backticks/double-quotes inside values.
        v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'{ck}: "{v_safe}"')
    return "{{ " + ", ".join(items) + " }}"


def rewrite_asset_url(v):
    """Rewrite ref CDN/image-optimizer URLs to local public asset paths.

    Images: mirror scripts/extract/asset-download.sh exactly — strip the
    /cdn-cgi/image/<resize-spec>/ prefix and PRESERVE the remaining
    subdirectory structure (e.g. images/pyramid/broccoli.webp). The downloader
    places files at impl/public/<that path>, so flattening to basename (the
    old behavior) 404s every image served from a subdir.

    Videos: extract-assets.sh places them flat at impl/public/videos/<basename>,
    so keep the /videos/<basename> routing for those.
    """
    if not isinstance(v, str) or not v:
        return v
    # Path portion only (drop query/fragment), then strip scheme://host.
    path = v.split("?", 1)[0].split("#", 1)[0]
    m_host = re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+(/.*)$', path)
    if m_host:
        path = m_host.group(1)
    # Strip Cloudflare image-resize prefix: /cdn-cgi/image/<resize-spec>/<realpath>
    idx = path.find("/cdn-cgi/image/")
    if idx != -1:
        after = idx + len("/cdn-cgi/image/")
        slash = path.find("/", after)
        path = path[slash:] if slash >= 0 else path
    rel = path.lstrip("/")
    base = os.path.basename(rel)
    ext = os.path.splitext(base)[1].lower()
    if ext in (".mp4", ".webm", ".mov"):
        return f"/videos/{base}"
    if ext in (".webp", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".avif"):
        return f"/{rel}" if rel else v
    return v


def rewrite_srcset(v):
    """Rewrite each srcset candidate URL while preserving descriptors."""
    candidates = []
    for raw_candidate in re.split(r",\s+", v):
        candidate = raw_candidate.strip()
        if not candidate:
            continue
        parts = candidate.split()
        rewritten = rewrite_asset_url(parts[0])
        descriptor = " ".join(parts[1:])
        candidates.append(" ".join(part for part in (rewritten, descriptor) if part))
    return ", ".join(candidates)


_LAZY_SRC_KEYS = ("data-src", "data-lazy-src", "data-original", "data-url", "data-lazy")
_LAZY_SRCSET_KEYS = ("data-srcset", "data-lazy-srcset")


def _is_placeholder_src(v):
    """U1 — true when an <img>/<source> `src` is missing or a non-real
    placeholder (inline data: URI, 1x1 spacer, blank gif). Lazy-loaded media
    keeps its real URL in data-src/data-srcset until an IntersectionObserver
    fires; the static capture never fires it, so `src` is still a placeholder."""
    if not isinstance(v, str):
        return True
    v = v.strip()
    if not v:
        return True
    if v.startswith("data:"):
        return True
    low = v.lower()
    return any(tok in low for tok in ("1x1", "spacer", "blank.gif", "placeholder", "transparent.png"))


def _lazy_resolved(node):
    """U1 — promote a lazy <img>/<source>'s real URL (data-src / data-srcset and
    common aliases) onto src / srcset when the eager value is missing or a
    placeholder, so the asset downloads and renders instead of staying blank.
    Generalizes image capture beyond <img src>+srcset to lazy galleries."""
    out = {}
    if not isinstance(node, dict):
        return out
    tag = (node.get("tag") or "").lower()
    if tag not in ("img", "source", "video"):
        return out
    if tag in ("img", "source"):
        if _is_placeholder_src(node.get("src")):
            for k in _LAZY_SRC_KEYS:
                cand = node.get(k)
                if isinstance(cand, str) and cand.strip() and not _is_placeholder_src(cand):
                    out["src"] = cand.strip()
                    break
        cur_srcset = node.get("srcset")
        if not (isinstance(cur_srcset, str) and cur_srcset.strip()):
            for k in _LAZY_SRCSET_KEYS:
                cand = node.get(k)
                if isinstance(cand, str) and cand.strip():
                    out["srcset"] = cand.strip()
                    break
    elif tag == "video" and _is_placeholder_src(node.get("poster")):
        cand = node.get("data-poster")
        if isinstance(cand, str) and cand.strip() and not _is_placeholder_src(cand):
            out["poster"] = cand.strip()
    return out


def escape_jsx_text(t):
    """Escape characters that would be parsed as JSX in text content."""
    if not t:
        return ""
    return (
        t.replace("\\", "\\\\")
         .replace("{", "&#123;")
         .replace("}", "&#125;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


def _text_jsx(t):
    """Escape JSX text and turn captured line breaks (\\n, from a <br> in the
    ref) into real <br /> elements so multi-line copy is not run together."""
    if not t:
        return ""
    return "<br />".join(escape_jsx_text(p) for p in t.split("\n"))


def safe_class_name(cls):
    """JSX className string — strip newlines, double-quotes."""
    return (cls or "").replace("\n", " ").replace('"', "'").strip()[:120]


RENDERED_IDS = set()  # id() of nodes actually emitted — drives the uncovered-text catch-all
REVEAL_RESETS = [0]  # count of scroll/load opacity-reveal resets — flags reveal sections

# Fix 89 — PARENT_MAP: id(child) → parent node.  Built once over the full DOM
# before section matching.  Used by find_subtree_for_section to detect when a
# matched section lives inside an anonymous wrapper (no class/id): return the
# wrapper so its siblings (e.g. a companion video div beside a hero section) are
# rendered together and never re-emitted by the _collect_uncovered pass.
PARENT_MAP: dict = {}

def _build_parent_map(node, parent=None):
    if not isinstance(node, dict):
        return
    if parent is not None:
        PARENT_MAP[id(node)] = parent
    for c in node.get("children") or []:
        _build_parent_map(c, node)


def _height_should_unfreeze(node, styles):
    """Fix 20/21 — True when a frozen px height on `node` would clip/overlap its
    content because the impl reflows it taller than capture time (observed on
    realfood hero titles: 56-68px overflow). Fires for any element that holds
    growable content (direct text or child elements). Guards keep the height
    for: intentional clip/reveal masks (overflow:hidden, e.g. a collapsed FAQ),
    replaced/intrinsic-sized elements (img/video/svg — height is geometry), and
    empty structural spacers (no text, no children). Width is never touched — it
    drives wrapping / line-break geometry. The caller converts height to a
    min-height floor rather than dropping it, so full-bleed sections keep their
    intended height while text can still grow."""
    if (styles.get("overflow") or "").strip() == "hidden":
        return False
    if node.get("svg") or (node.get("tag") or "").lower() in REPLACED_TAGS:
        return False
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        return True
    return any(isinstance(c, dict) for c in (node.get("children") or []))


def _is_scroll_state_translation(tv):
    """Fix 21 — True when `transform` is a pure px translation (no rotate/scale/
    skew) with a non-trivial offset. Scroll-scrub / parallax / stagger reveals
    are driven by JS that writes such transforms inline (no CSS transition or
    animation-name marker to detect them by), and they were captured mid-motion
    — freezing them displaces the clone (realfood pyramid categories were shoved
    -37 to -81px sideways). Percentage translations (translate(-50%,-50%)
    centering) and any rotate/scale/skew are treated as static design and kept."""
    if not tv or tv == "none" or "%" in tv:
        return False
    # Codex-review HIGH: a 4px floor over-stripped legitimate static layout
    # nudges (translateX(8px)) on other sites. Only LARGE marker-less px
    # translations look like a mid-scroll/parallax capture (realfood's frozen
    # reveals were 37-81px); small offsets are kept as static design.
    _MIN_PX = TRANSFORM_MIN_PX
    m = re.match(r"matrix\(([^)]*)\)", tv)
    if m:
        try:
            a, b, c, d, tx, ty = (float(x) for x in m.group(1).split(","))
        except ValueError:
            return False
        identity_linear = abs(a - 1) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1) < 1e-3
        return identity_linear and (abs(tx) >= _MIN_PX or abs(ty) >= _MIN_PX)
    if "translate" in tv and not any(fn in tv for fn in ("rotate", "scale", "skew", "matrix")):
        return any(abs(float(n)) >= _MIN_PX for n in re.findall(r"-?\d+\.?\d*", tv))
    return False


def _is_collapsed_reveal_transform(tv):
    """True when `transform` collapses the element to zero size (scaleX≈0 AND
    scaleY≈0 — e.g. matrix(0,0,0,0,...) or scale(0)). No static design renders an
    element at zero scale; it is always a captured entrance-animation INITIAL
    state written inline by JS (no CSS transition/animation marker to detect it
    by). An inverted-pyramid's food items were captured at matrix(0,0,0,0)+
    opacity:0 and rendered an EMPTY pyramid. Reset such a transform (and its
    companion opacity:0) to rest so the element is visible — the ref's own settle
    CSS confirms rest is visible (an item-class rule with opacity:1!important;
    transform:none!important). Distinct from a pure-translation parallax capture
    (handled by _is_scroll_state_translation)."""
    if not tv or tv == "none":
        return False
    m = re.match(r"matrix\(([^)]*)\)", tv)
    if m:
        try:
            a, b, c, d, tx, ty = (float(x) for x in m.group(1).split(","))
        except ValueError:
            return False
        # Fix 112 — require the FULL linear part to be ~0 (true collapse), not just
        # the diagonal: rotate(90deg)/rotate(270deg) resolve to matrix(0,±1,∓1,0,..)
        # with a≈0,d≈0 but b,c=±1, and must NOT be stripped as a zero-scale reveal.
        return (abs(a) < 1e-3 and abs(d) < 1e-3
                and abs(b) < 1e-3 and abs(c) < 1e-3)
    # scale(0) / scale(0,0) / scaleX(0) / scaleY(0) literal forms
    for sm in re.findall(r"scale[XY]?\(([^)]*)\)", tv):
        parts = [p.strip() for p in sm.split(",") if p.strip()]
        try:
            if parts and all(abs(float(p)) < 1e-3 for p in parts):
                return True
        except ValueError:
            continue
    return False


def _is_frozen_scrub_scale(tv):
    """True when `transform` is a pure, centered, uniform DOWN-scale (0<s<1, no
    translate/rotate/skew) — e.g. matrix(0.9,0,0,0.9,0,0) or scale(0.9). A
    marker-less sub-unity uniform scale on a captured style is a scroll-scrub /
    entrance INITIAL frame frozen by JS: a scroll-zoom background card was baked
    at scale 0.9 (the start of a band [.9,1,1,1]) and rendered shrunk to 90%
    (edge gaps) instead of zooming. The dominant/rest
    state across the band is scale 1, so reset the frozen scale to rest; if the
    generator then wraps it in <ScrollScrub scale=…> the real zoom drives it.
    Conservative: DOWN-scale only (up-scale can be legit emphasis) and no
    translation (a translate+scale combo may be intentional layout)."""
    if not tv or tv == "none":
        return False
    m = re.match(r"matrix\(([^)]*)\)", tv)
    if m:
        try:
            a, b, c, d, tx, ty = (float(x) for x in m.group(1).split(","))
        except ValueError:
            return False
        return (
            abs(b) < 1e-3 and abs(c) < 1e-3 and abs(tx) < 1e-3 and abs(ty) < 1e-3
            and abs(a - d) < 1e-3 and 1e-3 < a < 1 - 1e-3
        )
    m2 = re.fullmatch(r"scale\(\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)", tv.strip())
    if m2:
        try:
            sx = float(m2.group(1))
            sy = float(m2.group(2)) if m2.group(2) is not None else sx
        except ValueError:
            return False
        return abs(sx - sy) < 1e-3 and 1e-3 < sx < 1 - 1e-3
    return False


def _is_centering_transform(tv, styles):
    """Fix 68 — getComputedStyle resolves a static translate(-50%,-50%) centering
    transform to px matrix form (matrix(1,0,0,1,-W/2,-H/2)), so the '%' guard in
    _is_scroll_state_translation never fires on captured styles and the marker-
    less heuristic strips legitimate centering as a parallax state — displacing
    the element by half its own size (loop-129: the 1282x810 hero glow shifted
    +641/+405 and bled into the sections below; post-implement failed 10x).
    A pure translate that pulls the element back by exactly half its captured
    width/height (either axis; small tolerance) is centering — preserve it."""
    if not tv or "matrix" not in tv:
        return False
    m = re.match(r"matrix\(([^)]*)\)", tv)
    if not m:
        return False
    try:
        a, b, c, d, tx, ty = (float(x) for x in m.group(1).split(","))
    except ValueError:
        return False
    if not (abs(a - 1) < 1e-3 and abs(b) < 1e-3 and abs(c) < 1e-3 and abs(d - 1) < 1e-3):
        return False
    if tx == 0 and ty == 0:
        return False
    sty = styles or {}
    w = _px(sty.get("width"))   # _px is defined below; resolved at call time
    h = _px(sty.get("height"))

    def _is_half(offset, size):
        if offset == 0:
            return True  # axis unused
        if offset > 0 or size is None:
            return False  # centering pulls back (negative); unknown size can't verify
        return abs(-offset - size / 2) <= max(2.0, size * 0.02)

    return _is_half(tx, w) and _is_half(ty, h)


def _animation_state_targets(styles):
    """Fix 21 — the set of {transform, opacity} whose captured value is a
    mid-animation snapshot to reset to rest. An element animates a property when
    a transition lists it (or `all`) or any keyframe animation is attached; the
    computed value was then sampled at an arbitrary frame (scroll-reveal,
    parallax, stagger) — freezing it leaves the clone displaced or invisible.
    Static transforms with no transition/animation (e.g. translate(-50%,-50%)
    centering) are preserved."""
    tp = styles.get("transition-property") or ""
    an = (styles.get("animation-name") or "").strip()
    animated = bool(an and an != "none")
    out = set()
    if animated or "transform" in tp or "all" in tp:
        out.add("transform")
    if animated or "opacity" in tp or "all" in tp:
        out.add("opacity")
    return out


def render(node, indent=2, hover_rules=None):
    """Render a node (and its subtree) to JSX. Returns the string.

    Fix 19 — when hover_rules is a mutable list, any node with hover_styles
    gets an auto-generated class id appended to its className, and the CSS
    declarations are pushed to hover_rules. The caller emits a <style> tag
    at the top of the component body so :hover transitions captured by Fix
    16 actually have something to animate to.
    """
    if not isinstance(node, dict):
        return ""
    tag = (node.get("tag") or "div").lower()
    if tag in SKIP_TAGS:
        return ""
    RENDERED_IDS.add(id(node))
    text = node.get("text", "") or ""
    cls = safe_class_name(node.get("class", ""))
    styles = node.get("styles") or {}
    # Fix 110 — set when this element carried a frozen scroll-scrub scale (Fix 108);
    # stamped as data-scroll-scrub-target below so ScrollScrub can auto-wire it.
    _scrub_scale_target = False
    if styles:
        if _height_should_unfreeze(node, styles):
            # S1 — fold a negative bottom margin into the height→min-height floor.
            # A sticky-container section can overlap the following section with a
            # negative bottom margin (captured height H, margin-bottom -M). Keeping
            # the full captured height makes the box ~M px taller than the section's
            # real rendered extent while the margin still pulls siblings up — the
            # dominant "sections drift". eff_h shrinks the box to its real flow
            # contribution (H - M); neutralising the bottom margin keeps the next
            # section's flow position unchanged.
            _h = styles.get("height")
            eff_h = _effective_flow_height(_h, styles) if isinstance(_h, str) else None
            baked = eff_h is not None and eff_h != _h
            converted = {}
            for k, v in styles.items():
                if k == "max-height":
                    continue  # caps/clips content; overflow:hidden masks already excluded
                if k == "height":
                    converted.setdefault("min-height", eff_h if eff_h is not None else v)  # floor, not a clamp; keep any explicit min-height
                    continue
                converted[k] = v
            if baked:
                converted["min-height"] = eff_h  # override any captured min-height that also ignored the margin
                converted.pop("margin-bottom", None)
                converted["margin-bottom"] = "0px"  # last key wins over the `margin` shorthand's bottom
            styles = converted
        anim = _animation_state_targets(styles)
        tv = styles.get("transform")
        # A position:fixed / sticky element's transform parks or positions it
        # (e.g. an intro overlay translated fully off-screen). It is never a
        # scroll-scrub reveal, so the marker-less heuristic must NOT strip it —
        # doing so un-hides full-screen overlays that then cover the page. A real
        # CSS-animated transform (marker present) is still reset.
        _pos = (styles.get("position") or "").strip().lower()
        # Fix 68 — a centering translate (-50%,-50% resolved to px) is static
        # layout on BOTH reset paths: stripping it via the marker-less heuristic
        # OR via a transition/animation marker displaces the element by half its
        # own size, far worse than freezing any residual animation offset.
        _centering = _is_centering_transform(tv, styles)
        # A zero-scale collapse (matrix(0,0,0,0)/scale(0)) is a captured entrance
        # initial state that has no CSS marker — strip it like a parallax reveal
        # so the element renders at its visible rest state (empty-pyramid fix).
        _collapsed = _is_collapsed_reveal_transform(tv) and _pos not in ("fixed", "sticky")
        # A frozen sub-unity uniform scale (e.g. matrix(0.9) on a scroll-zoom
        # background) is a scrub/entrance initial frame — reset transform to rest
        # (scale 1); only the transform, not opacity (it is not hidden).
        # Fix 116 — only when the plan actually declares a scrollScrub scale band
        # (SCRUB_WRAP_ATTRS truthy). Without that context a sub-unity scale is a
        # deliberate static design choice (a 0.9-scaled badge/overlay/thumbnail),
        # NOT a scrub initial frame — stripping it then mis-sizes the element and
        # the auto-wrap (which also requires SCRUB_WRAP_ATTRS) never re-drives it.
        _frozen_scale = (
            _is_frozen_scrub_scale(tv)
            and _pos not in ("fixed", "sticky")
            and bool(SCRUB_WRAP_ATTRS)
        )
        if _frozen_scale:
            _scrub_scale_target = True
        _scroll_state = _is_scroll_state_translation(tv) and _pos not in ("fixed", "sticky")
        if tv and tv != "none" and not _centering and ("transform" in anim or _scroll_state or _collapsed or _frozen_scale):
            styles = {k: v for k, v in styles.items() if k != "transform"}
            # A pure frozen-scrub-scale target (only _frozen_scale true) is wrapped
            # in <ScrollScrub> via its stamp below; counting it as a REVEAL would
            # ALSO wrap it in <ScrollReveal> (double-wrap — the outer reveal's
            # transform fights the scrub scale). Only count genuine reveals.
            if "transform" in anim or _scroll_state or _collapsed:
                REVEAL_RESETS[0] += 1  # scroll/parallax transform reveal (marker-less too)
        ov = styles.get("opacity")
        # Reset a hidden opacity when the element fades in on scroll/load (marker)
        # OR when its transform collapsed it to zero scale (a baked entrance
        # initial state whose rest is visible).
        if ("opacity" in anim or _collapsed) and ov is not None:
            try:
                hidden = float(ov) < 1
            except (TypeError, ValueError):
                hidden = False
            if hidden:
                styles = {k: v for k, v in styles.items() if k != "opacity"}
                REVEAL_RESETS[0] += 1  # this element fades in on scroll/load
        # P5 — a large fixed px width is the desktop capture width; on a layout
        # container convert it to max-width + width:100% so the page reflows at
        # narrow viewports instead of overflowing. Replaced elements (img/video/
        # svg) keep their intrinsic width; small fixed widths (buttons/icons,
        # < 480px) are left alone.
        _w = styles.get("width", "")
        if tag in REPLACED_TAGS and isinstance(_w, str) and _w.endswith("px"):
            # Responsive media: keep the intrinsic width as the natural size but
            # cap at the container so images/videos scale down on narrow screens;
            # auto height preserves aspect ratio while scaling.
            styles.setdefault("max-width", "100%")
            if isinstance(styles.get("height"), str) and styles["height"].endswith("px"):
                styles["height"] = "auto"
        elif isinstance(_w, str) and _w.endswith("px"):
            try:
                _wpx = float(_w[:-2])
            except ValueError:
                _wpx = 0.0
            # A fixed width >= a phone viewport is a layout container's desktop
            # capture width — make it reflow. Smaller fixed widths (buttons/
            # icons/pills) stay put.
            if _wpx >= REFLOW_CHILD_MIN_PX:
                styles = {k: v for k, v in styles.items() if k != "width"}
                styles["max-width"] = _w
                styles["width"] = "100%"
    children = node.get("children") or []
    # Fix 27 — if this node's subtree is a split-text animation (per-character
    # spans), collapse it to clean visible text so words aren't run together
    # ("RealFoodcansolvethiscrisis" -> "Real Food can solve this crisis").
    if children and not (isinstance(text, str) and text.strip()):
        collapsed = _split_text_collapse(node)
        if collapsed:
            text = collapsed
            children = []

    # P7 — fill a genuinely-empty element (no text, no children) with its JS
    # count-up final value from runtime-text.json, matched by class token in
    # document order. Never overwrites existing text/children.
    if RUNTIME_TEXT and not (isinstance(text, str) and text.strip()) and not children:
        for _rk, _rvals in RUNTIME_TEXT.items():
            if _rk in cls:
                _i = RUNTIME_TEXT_IDX.get(_rk, 0)
                if _i < len(_rvals):
                    text = _rvals[_i]
                    RUNTIME_TEXT_IDX[_rk] = _i + 1
                break

    # Fix 19 — auto-add hover-trigger class when hover_styles present.
    hover_styles = node.get("hover_styles") if isinstance(node, dict) else None
    if hover_styles and isinstance(hover_rules, list):
        hov_id = f"h_{len(hover_rules)}"
        hover_rules.append((hov_id, hover_styles))
        cls = (cls + " " + hov_id).strip() if cls else hov_id

    pad = "  " * indent
    cls_attr = f' className="{cls}"' if cls else ""
    style_attr = ""
    if styles:
        style_attr = f" style={style_to_jsx(styles)}"

    # Asset/link attributes captured by extract-dom (Fix 16c). Emit each as a
    # JSX attribute with the HTML→JSX rename where needed. Skip empty values.
    # Without these, <img>/<a>/<video> render as blank placeholders even when
    # the assets exist in impl/public/ — dominant cause of inflated AE on
    # image-heavy sections.
    attr_map = {
        "id": "id",  # section anchors — section-map + section-compare locate by id
        "src": "src", "href": "href", "alt": "alt", "poster": "poster",
        "srcset": "srcSet", "sizes": "sizes", "type": "type",
        "target": "target", "rel": "rel",
        "aria-label": "aria-label", "title": "title", "role": "role",
    }
    # U1 — data-src/data-srcset/data-poster are capture-time lazy artifacts the
    # clone never re-runs; their real URLs are promoted onto src/srcset/poster
    # below, so they are dropped from emission (emitting them is dead weight and
    # would over-fetch a never-displayed URL).
    attr_emit: dict[str, str] = {}
    lazy = _lazy_resolved(node)  # U1 — data-src/data-srcset/data-poster promoted
    for src_key, jsx_key in attr_map.items():
        v = lazy.get(src_key, node.get(src_key))
        if not isinstance(v, str) or not v:
            continue
        # For image/video URLs rewrite the CDN optimizer path to the
        # locally-downloaded path under /images/ or /videos/ so Next.js serves
        # from impl/public. srcset needs the same treatment: if it keeps a
        # /cdn-cgi/image/... candidate, the browser will pick that broken
        # runtime path even when src itself is correct.
        if src_key in ("src", "poster"):
            v = rewrite_asset_url(v)
        elif src_key == "srcset":
            v = rewrite_srcset(v)
        attr_emit[jsx_key] = v

    if tag in SVG_TAGS or node.get("svg"):
        # Use_href + xlinkHref both shipped so refs across SVG <use>
        # work whether the captured side used href or xlink:href.
        for src_key in list(SVG_ATTR_RENAMES.keys()) + list(SVG_PASSTHROUGH_ATTRS):
            v = node.get(src_key)
            if not isinstance(v, str) or not v:
                continue
            jsx_key = SVG_ATTR_RENAMES.get(src_key, src_key)
            # If the value contains a url(...) reference (mask, filter,
            # clipPath, fill, stroke, href on <use>), rewrite to the
            # locally-downloaded asset path so the runtime resolves
            # against impl/public/ instead of the ref's CDN.
            if "url(" in v:
                v = rewrite_css_urls(v)
            elif src_key in {"href", "xlink:href"} and v.startswith(
                ("http://", "https://"),
            ):
                v = rewrite_asset_url(v)
            attr_emit[jsx_key] = v

    # Fix 74 — stamp elements frozen at the spec's inactive state opacity (no
    # CSS opacity transition: Fix 21 already resets transitioned ones) so the
    # emitted ScrollStateDriver can animate them to the active state.
    if (
        SCROLL_FADE_FROM is not None
        and tag not in REPLACED_TAGS
        and not node.get("svg")
    ):
        try:
            _opv = float(styles.get("opacity")) if styles.get("opacity") is not None else None
        except (TypeError, ValueError):
            _opv = None
        if _opv is not None and abs(_opv - SCROLL_FADE_FROM) <= 0.05:
            _tp_fade = styles.get("transition-property") or ""
            if "opacity" not in _tp_fade and "all" not in _tp_fade:
                attr_emit["data-scroll-fade"] = "1"
                SCROLL_FADE_STAMPED[0] += 1

    # Fix 76 — stamp draw-in SVG paths: a path the ref draws via
    # strokeDashoffset is captured frozen WITH a stroke-dasharray (the
    # JS-prepared state). Paths without a dasharray are static art.
    # Fix 114 — (a) also handle <line>/<polyline>/<polygon> (an inverted-pyramid
    # outline is drawn with <line>, not <path>); (b) when stroke-dashoffset ≈
    # stroke-dasharray the shape is fully HIDDEN (the undrawn draw-in initial
    # frame), so stamp it even if transition-spec missed the animation.
    _da = node.get("stroke-dasharray") or styles.get("stroke-dasharray")
    _do = node.get("stroke-dashoffset") or styles.get("stroke-dashoffset")

    def _stroke_num(v):
        try:
            return float(str(v).strip().rstrip("px"))
        except (TypeError, ValueError):
            return None

    _hidden_draw = False
    if _da and _do:
        _dav, _dov = _stroke_num(_da), _stroke_num(_do)
        _hidden_draw = (
            _dav is not None and _dov is not None and _dav > 0
            and abs(_dav - _dov) <= max(0.5, _dav * 0.02)
        )
    if (
        tag in ("path", "line", "polyline", "polygon")
        and (node.get("stroke") or styles.get("stroke"))
        and _da
        and (STROKE_DRAW_SPEC or _hidden_draw)
    ):
        attr_emit["data-stroke-draw"] = "1"
        STROKE_DRAW_STAMPED[0] += 1

    # Fix 110 — stamp the scroll-scrub SCALE target. Fix 108 detected this element
    # was frozen at a sub-unity scale (the captured start of a scroll-zoom band)
    # and reset it to rest; mark it so <ScrollScrub> can auto-discover and drive it
    # (closes the "which element does the scale band target?" gap that left agents
    # guessing and the #3 zoom unwired). Layout-inert data attributes.
    if _scrub_scale_target:
        attr_emit["data-scroll-scrub-target"] = "1"
        attr_emit["data-scroll-scrub-prop"] = "scale"
        SCRUB_SCALE_STAMPED[0] += 1

    # A value containing a double quote or backslash (e.g. mask='url("#id")')
    # cannot sit inside a double-quoted JSX attribute — \" is invalid there and
    # breaks esbuild. Emit those as a JS-string expression `attr={"..."}`;
    # plain values keep the simpler double-quoted form.
    extra_attrs = "".join(
        (f" {k}={{{json.dumps(v)}}}" if ('"' in v or "\\" in v) else f' {k}="{v}"')
        for k, v in attr_emit.items()
    )
    # P7 — an autoplay background <video> needs playback attributes to actually
    # run (a bare <video src> stays paused). Emit autoPlay/muted/loop/playsInline
    # from assets.json; non-autoplay videos (click-to-play) are left untouched.
    if tag == "video":
        _vsrc = node.get("src") or ""
        _vbn = _vsrc.split("?", 1)[0].rstrip("/").split("/")[-1]
        _vp = VIDEO_PROPS.get(_vbn)
        if _vp and _vp.get("autoplay"):
            # muted + playsInline are required for autoplay to start in browsers
            extra_attrs += " autoPlay muted playsInline"
            if _vp.get("loop"):
                extra_attrs += " loop"
    cls_attr += extra_attrs

    # Fix 18 — pseudo-element synthesis. When extract-dom captured a non-
    # empty before_styles/after_styles on this node, emit a <span> child
    # carrying those styles plus the original `content` value as visible
    # text (since CSS `content: "★"` can't be expressed via inline style
    # alone in React). The data-pseudo attribute lets later CSS overrides
    # target these synthetic siblings if needed. Position: absolute on the
    # ref pseudo is preserved via the inline style copy.
    pseudo_jsx = ""

    def _render_pseudo(which, ps_dict, child_indent):
        if not isinstance(ps_dict, dict) or not ps_dict:
            return ""
        # Strip CSS-quoted content: "foo" → foo. Empty string content (").
        raw_content = ps_dict.get("content", "")
        text_content = ""
        if isinstance(raw_content, str):
            stripped = raw_content.strip()
            if stripped.startswith('"') and stripped.endswith('"'):
                text_content = stripped[1:-1]
            elif stripped not in ("none", "normal", "''", '""'):
                text_content = stripped
        ps_styles = {k: v for k, v in ps_dict.items() if k != "content"}
        if not ps_styles and not text_content:
            return ""
        ps_pad = "  " * child_indent
        ps_style_attr = f" style={style_to_jsx(ps_styles)}" if ps_styles else ""
        body = escape_jsx_text(text_content) if text_content else ""
        return f'{ps_pad}<span data-pseudo="{which}"{ps_style_attr}>{body}</span>'

    before_ps = node.get("before_styles")
    after_ps = node.get("after_styles")
    if before_ps:
        pseudo_jsx += "\n" + _render_pseudo("before", before_ps, indent + 1)
    if after_ps:
        pseudo_jsx += ("\n" if not pseudo_jsx else "") + \
                      _render_pseudo("after", after_ps, indent + 1)

    # Void element — self-close, no children/text.
    if tag in VOID_TAGS:
        return f'{pad}<{tag}{cls_attr}{style_attr} />'

    # Render children first.
    child_str = ""
    if children or pseudo_jsx:
        rendered_chunks = []
        for c in children:
            r = render(c, indent + 1, hover_rules)
            if not r:
                continue
            # Fix 22 — restore the whitespace text node that sat between this
            # inline element and its next sibling (word-split spans). JSX
            # collapses formatting whitespace between elements, so emit an
            # explicit {' '} or the words run together ("Forthe" / "Forthefirst").
            if isinstance(c, dict) and c.get("wsAfter"):
                r = r + "{' '}"
            rendered_chunks.append(r)
        if pseudo_jsx:
            # ::before precedes the real children, ::after follows them.
            if before_ps:
                rendered_chunks.insert(0, _render_pseudo("before", before_ps, indent + 1))
            if after_ps:
                rendered_chunks.append(_render_pseudo("after", after_ps, indent + 1))
        child_str = "\n" + "\n".join(rendered_chunks) + "\n" + pad

    # Text content (verbatim, escaped).
    if text and not children and not pseudo_jsx:
        return f'{pad}<{tag}{cls_attr}{style_attr}>{_text_jsx(text)}</{tag}>'
    if text and (children or pseudo_jsx):
        # Mixed: text + children. Place text at top, then children/pseudos.
        return f'{pad}<{tag}{cls_attr}{style_attr}>\n{"  " * (indent + 1)}{_text_jsx(text)}{child_str}</{tag}>'
    return f'{pad}<{tag}{cls_attr}{style_attr}>{child_str}</{tag}>'


def section_component_name(section, index):
    """Derive a PascalCase component name from section metadata."""
    sid = (section.get("id") or "").strip()
    cls = (section.get("cls") or section.get("className") or "").strip()
    name = sid or cls.split()[0] if cls else f"Section{index}"
    # Strip CSS Module hash suffixes like prefix_hero__AjMaf → PrefixHero.
    name = re.sub(r"__\w+$", "", name)
    # Replace separators.
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_") or f"Section{index}"
    # PascalCase.
    parts = name.split("_")
    pascal = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not pascal or not pascal[0].isalpha():
        pascal = f"Section{index}"
    return pascal


def _split_text_collapse(node):
    """Fix 27 — Framer/GSAP split-text animations wrap each character (and word)
    in its own span: "Real Food" becomes <span><span>R</span><span>e</span>...
    The wrappers carry no direct text, and rendering the nested char-spans drops
    the inter-word spaces, so the clone shows "RealFoodcansolvethiscrisis". When
    a subtree is a split-text wrapper — many leaf spans, almost all a single
    visible character — return the reassembled visible text (nbsp normalised,
    whitespace collapsed) so the clone shows clean copy. Returns None otherwise.
    The disintegrate/scrub animation is re-applied as a later enhancement;
    correct text beats animated-but-broken text."""
    leaves = []
    # Interactive/structural tags whose presence means this is NOT a split-text
    # run (a nav/link/button list of one-word items looks like a word-split but
    # must be preserved). Guards the weaker word-split signal below.
    _INTERACTIVE = {
        "a", "button", "input", "select", "textarea", "summary", "details",
        "label", "img", "svg", "video", "iframe", "li",
    }
    has_interactive = [False]

    def collect(n):
        if not isinstance(n, dict):
            return
        if (n.get("tag") or "").lower() in _INTERACTIVE:
            has_interactive[0] = True
        kids = n.get("children") or []
        t = n.get("text")
        if isinstance(t, str) and not kids:
            leaves.append(t)  # keep whitespace-only leaves — they are word gaps
        for c in kids:
            collect(c)

    collect(node)
    visible = [leaf for leaf in leaves if leaf.strip()]
    if len(visible) < 10:
        return None
    # Icon-font / symbol-glyph runs (single PUA or punctuation chars) look like a
    # split but carry no real text — collapsing them yields garbage, and a node
    # mixing real text with an icon row must not collapse wholesale. Require most
    # leaves to contain a real letter; CJK letters count, so CJK split-text still
    # collapses while icon rows (and mixed sections) are left for the child pass.
    if sum(1 for leaf in visible if any(ch.isalpha() for ch in leaf)) / len(visible) < SPLIT_TEXT_CHAR_RATIO:
        return None
    single = sum(1 for leaf in visible if len(leaf.strip()) <= 1)
    char_split = single / len(visible) >= SPLIT_TEXT_CHAR_RATIO
    if char_split:
        # Render-verified defect fix — per-WORD span groups of per-CHAR spans
        # ("Real Food can solve this crisis."): the words' separator spans are
        # captured EMPTY (their lone space was trimmed at capture), so a flat
        # join runs the words together. The word boundary survives
        # STRUCTURALLY: each word's chars live under their own direct child.
        # Join chars within a group; join groups with spaces — but only when
        # >=2 groups look like words (>=2 chars), so a FLAT char split (every
        # direct child a single char) keeps the original flat join.
        # Recursive assembly handles nested split levels (line > word > char):
        # at any level whose children yield >=2 multi-char parts (words/lines),
        # join with spaces; a level of single-char parts (the chars of one
        # word) keeps the flat join, so a FLAT per-char heading reassembles
        # unchanged.
        def _assemble(n):
            kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
            own = n.get("text") if isinstance(n.get("text"), str) else ""
            if not kids:
                return own
            parts = [_assemble(c) for c in kids]
            nonempty = [p for p in parts if p.strip()]
            wordish = [p for p in nonempty if len(p.strip()) >= 2]
            if len(wordish) >= 2:
                joined = " ".join(p.strip() for p in nonempty)
            else:
                joined = "".join(parts)
            return own + joined

        text = _assemble(node).replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip() or None
    # Word-split (Framer/GSAP split-by-words): many one-word leaf spans, each a
    # single whitespace-free token. Weaker signal than char-split, so require a
    # higher count and reject any interactive/structural content. Join with
    # spaces (word spans usually carry no whitespace text nodes between them).
    single_word = sum(1 for leaf in visible if " " not in leaf.strip())
    word_split = len(visible) >= SPLIT_TEXT_MIN_LEAVES and single_word / len(visible) >= SPLIT_TEXT_CHAR_RATIO
    if not word_split or has_interactive[0]:
        return None
    text = " ".join(leaf.strip() for leaf in visible).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip() or None


def _find_relative_ancestor(root, target):
    """Fix 26 — nearest ancestor of `target` (same object) whose position is
    relative. A position:sticky section root, emitted as its own flat component,
    loses the relative section wrapper that bounds its pin, so it sticks to the
    page body and never releases. Re-emitting that wrapper around the component
    restores the scroll range."""
    found = [None]

    def walk(node, ancestors):
        if node is target:
            for anc in reversed(ancestors):
                if isinstance(anc, dict) and (anc.get("styles") or {}).get("position") == "relative":
                    found[0] = anc
                    return True
            return True
        if isinstance(node, dict):
            for c in node.get("children") or []:
                if walk(c, ancestors + [node]):
                    return True
        return False

    walk(root, [])
    return found[0]


def _nearest_ancestor_bg(root, target):
    """Fix 88 — nearest NON-ROOT ancestor's solid background-color for
    `target` (same object). The ref wraps mid-page sections in a colored band
    (an off-black dark wrapper); flat section emission drops the wrapper and
    its backdrop, leaving white copy invisible on the cream page. The root
    (body) is excluded — the page base is painted by the global override."""
    found = [None]

    def walk(node, ancestors):
        if node is target:
            for anc in reversed(ancestors[1:]):
                bg = ((anc.get("styles") or {}).get("background-color") or "").strip()
                if bg and bg not in ("none", "transparent", "rgba(0, 0, 0, 0)"):
                    found[0] = bg
                    return True
            return True
        if isinstance(node, dict):
            for c in node.get("children") or []:
                if walk(c, ancestors + [node]):
                    return True
        return False

    walk(root, [])
    return found[0]


def _px(v):
    """Parse a `<n>px` CSS length to float, else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v.endswith("px"):
        return None
    try:
        return float(v[:-2])
    except ValueError:
        return None


def _bottom_margin_px(styles):
    """Bottom margin of a node in px, from `margin-bottom` longhand or the
    `margin` shorthand (1/2/3/4-value forms). None when not expressible in px."""
    styles = styles or {}
    mb = _px(styles.get("margin-bottom"))
    if mb is not None:
        return mb
    short = styles.get("margin")
    if not isinstance(short, str):
        return None
    parts = short.split()
    # shorthand bottom: 1->[0], 2->[0], 3->[2], 4->[2]
    idx = {1: 0, 2: 0, 3: 2, 4: 2}.get(len(parts))
    if idx is None:
        return None
    return _px(parts[idx])


def _effective_flow_height(height, styles):
    """S1 — the re-emitted relative ancestor wrapper (Fix 26) bounds a sticky
    pin's scroll range via a min-height floor. Using the captured `height`
    verbatim ignores a negative bottom margin, which in the ref overlaps the
    next section (height H + margin-bottom -M → H-M of real flow). Dropping the
    overlap inflates the wrapper by the margin and drifts everything below down.
    Bake a negative bottom margin into the floor so the wrapper measures its
    real flow contribution. Positive/zero margins and non-px values leave the
    captured height unchanged."""
    h = _px(height)
    mb = _bottom_margin_px(styles)
    if h is None or mb is None or mb >= 0:
        return height
    return f"{int(round(h + mb))}px"


def find_subtree_for_section(root, section, consumed):
    """Locate the structure.json subtree corresponding to a section-map entry.
    Match by tag + class (or id). Returns the first match not yet consumed.

    Fix 16b — V13 (11672af) regressed to ae_avg 881k because this function
    used to return the *first* DOM match per section, regardless of whether
    that subtree had already been assigned to an earlier section. When
    section-map.json contained multiple entries sharing a class prefix
    (CSS-Module suffixes like prefix_section__hash-2, -3, ...), 8 of 15
    sections collapsed to the same subtree and rendered identical JSX —
    section-compare scored every duplicate at ~1.2M AE (the max possible).
    The `consumed` set tracks Python id() of already-assigned subtrees so
    each section gets a unique slice of the DOM.
    """
    sid = section.get("id")
    cls = section.get("cls") or section.get("className") or ""
    target_tag = (section.get("tag") or "section").lower()
    target_tokens = cls.split()
    target_cls = target_tokens[0] if target_tokens else ""

    def walk(node, match_tag, id_only=False, all_tokens=False, id_and_cls=False):
        if not isinstance(node, dict) or id(node) in consumed:
            return None
        # match_tag=False relaxes the tag constraint (fallback pass): decode
        # normalises many section roots to <section>, but the captured scaffold
        # may carry the same class/id on a <div>/<main>/<header>. Identity is
        # the id/class, not the tag.
        tag_ok = (not match_tag) or node.get("tag", "").lower() == target_tag
        node_id = node.get("id")
        # Fix 84 — repeated CSS-module classes: a class-only entry must not
        # consume a node whose id is reserved by ANOTHER section entry.
        id_reserved_for_other = (
            node_id and node_id != sid and node_id in RESERVED_SECTION_IDS
        )
        node_cls = node.get("class", "") or ""
        node_tokens = node_cls.split()
        if id_and_cls:
            # Fix 90 — combined id+cls match (most-specific): both the section
            # id AND the first class token must match.  Prevents two sections
            # that share the same id (e.g. two entries both carrying id="footer")
            # from ever landing on each other's subtree regardless of consumed-set
            # state.
            hit = (
                bool(sid) and node_id == sid
                and bool(target_cls) and target_cls in node_tokens
            )
        elif id_only:
            hit = bool(sid) and node_id == sid
        elif all_tokens:
            hit = bool(target_tokens) and all(t in node_tokens for t in target_tokens)
        else:
            hit = (
                (sid and node_id == sid)
                or (target_cls and target_cls in node_tokens)
            )
        if tag_ok and not id_reserved_for_other and hit:
            return node
        for c in node.get("children", []) or []:
            m = walk(c, match_tag, id_only, all_tokens, id_and_cls)
            if m:
                return m
        return None

    # Fix 84 — most-specific-first resolution. (a) ID-FIRST: an entry that
    # names an id must take its id node even when a same-classed fragment
    # appears EARLIER in document order (the faqs collapse: a 136px
    # sections_text fragment sharing the section class shadowed the real
    # id=faqs node in the OR-match walk). (b) ALL-TOKENS next: a multi-class
    # entry (section + cta) must prefer the node carrying EVERY token before
    # falling back to the ambiguous first token.
    # Fix 90 — id+cls combined match is tried first when both are available:
    # this is the most-specific selector and avoids shared-id collision when two
    # section-map entries carry the same DOM id but different class signatures
    # (e.g. a footer-bar section and a carousel section both marked id="footer").
    found = None
    if sid and target_cls:
        found = walk(root, True, id_and_cls=True)
        if found is None:
            found = walk(root, False, id_and_cls=True)
    if found is None and sid:
        found = walk(root, True, id_only=True)
        if found is None:
            found = walk(root, False, id_only=True)
    if found is None and len(target_tokens) > 1:
        found = walk(root, True, all_tokens=True)
        if found is None:
            found = walk(root, False, all_tokens=True)
    # Strict tag+identity match first; only on miss retry ignoring tag. The
    # fallback fires solely on the None path, so it never reassigns a subtree an
    # earlier strict match already claimed — it can only recover sections that
    # would otherwise emit an empty subtree-not-found stub (text/identity loss).
    if found is None:
        found = walk(root, True)
    if found is None:
        found = walk(root, False)
    if found is not None:
        consumed.add(id(found))
        # Fix 89 — anonymous-wrapper promotion.  When the matched section lives
        # directly inside an anonymous container (no class, not a section-map
        # id), return the container as the subtree so that its companion
        # siblings (e.g. a video div beside a hero section) are rendered
        # together and never re-emitted as _UncoveredAfter* fragments.
        parent = PARENT_MAP.get(id(found))
        if (
            parent is not None
            # Promote only when parent is an anonymous grouping wrapper:
            #   1. No class (not a named structural element).
            #   2. id (if any) is not a section-map section id — DOM ids like
            #      "intro" are fine; section ids like "faqs" are not.
            #   3. Not already consumed by an earlier section.
            #   4. No sibling of `found` is itself a named section-map entry.
            #      If a sibling has a section class or id, the parent is a
            #      multi-section container and must NOT be absorbed wholesale —
            #      that would swallow the sibling and produce a subtree-not-found
            #      for the next section (the test_uncovered regression case).
            and not (parent.get("class") or "").strip()
            and (parent.get("id") or "") not in RESERVED_SECTION_IDS
            and id(parent) not in consumed
            # Only promote when there is at least one NON-SECTION sibling to
            # absorb.  A lone section with no siblings gains nothing from
            # promotion and must not inherit the parent's frozen body height.
            and any(
                isinstance(sib, dict) and sib is not found
                for sib in (parent.get("children") or [])
            )
            # Bail if any sibling is itself a named section-map entry — the
            # parent is a multi-section container and must not be absorbed.
            and not any(
                isinstance(sib, dict) and sib is not found and (
                    (sib.get("id") or "") in RESERVED_SECTION_IDS
                    or any(
                        fc and fc in (sib.get("class") or "")
                        for fc in SECTION_FIRST_CLASSES
                    )
                )
                for sib in (parent.get("children") or [])
            )
        ):
            consumed.discard(id(found))
            consumed.add(id(parent))
            found = parent
    return found


structure = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out_dir = Path(sys.argv[3])

# Fix 74 — scroll-position-state fade. A JS-driven state fade (e.g.
# animate:{opacity: a?1:.5}) leaves no CSS transition marker, so elements are
# captured FROZEN at the inactive opacity and the clone never produces a
# runtime delta when the trigger is driven (transition-fires). When
# transition-spec declares such an entry, stamp matching frozen elements with
# data-scroll-fade during render; the emitted ScrollStateDriver animates them
# to the active state with the spec's real duration/ease.
# Fix 80 — capture viewport height (orig-layout.json). Refs author sticky
# scroll tracks in vh (e.g. height:300vh; margin-bottom:-75vh); the capture
# resolves them to px at THIS viewport, and freezing the px renders the wrong
# geometry at any other viewport (live: 300vh = 1899 at a 633 viewport, the
# frozen 2700 is +800 over — the dominant sections-drift). Known viewport lets
# the emitter re-express near-exact vh multiples back into vh.
CAPTURE_VPH = None
try:
    _ol = json.loads((Path(sys.argv[1]).parent / "orig-layout.json").read_text(encoding="utf-8"))
    _vph = _ol.get("viewportHeight") if isinstance(_ol, dict) else None
    if isinstance(_vph, (int, float)) and not isinstance(_vph, bool) and _vph > 0:
        CAPTURE_VPH = float(_vph)
except (OSError, json.JSONDecodeError):
    CAPTURE_VPH = None


def _vh_or_px(value):
    """Re-express a captured px length as vh when it is a near-exact multiple
    of 25vh at the capture viewport and at least 50vh — the authored-track
    shapes (75/100/200/225/300vh). Everything else (content-derived px like a
    638px hero) stays px. Returns the original value when no viewport is
    recorded or the value is not such a multiple."""
    if CAPTURE_VPH is None:
        return value
    px = _px(value) if isinstance(value, str) else None
    if px is None or px <= 0:
        return value
    ratio_vh = px / CAPTURE_VPH * 100.0
    nearest = round(ratio_vh / 25.0) * 25
    if nearest < 50:
        return value
    expected_px = nearest / 100.0 * CAPTURE_VPH
    if abs(px - expected_px) <= max(1.0, expected_px * 0.005):
        return f"{int(nearest)}vh"
    return value


SCROLL_FADE_FROM = None
SCROLL_FADE_STAMPED = [0]
# Fix 110 — count of elements stamped data-scroll-scrub-target (frozen scroll-zoom
# scale targets, detected by _is_frozen_scrub_scale) so ScrollScrub auto-wires them.
SCRUB_SCALE_STAMPED = [0]
# Fix 76 — same architecture for the spec's strokeDashoffset draw-in: paths the
# ref draws in are captured frozen WITH a stroke-dasharray (the JS-prepared
# draw state). Stamp them data-stroke-draw; the driver animates the draw.
STROKE_DRAW_SPEC = False
STROKE_DRAW_STAMPED = [0]
try:
    _ts = json.loads((Path(sys.argv[1]).parent / "transition-spec.json").read_text(encoding="utf-8"))
    for _t in (_ts.get("transitions") or []) if isinstance(_ts, dict) else []:
        if not isinstance(_t, dict):
            continue
        _hint = " ".join(str(_t.get(_k) or "") for _k in ("trigger", "bundle_branch", "id")).lower()
        _anim = _t.get("animation")
        _prop = str(_anim.get("property") or "").lower() if isinstance(_anim, dict) else ""
        if "strokedashoffset" in _hint.replace("-", "") or "strokedashoffset" in _prop.replace("-", ""):
            STROKE_DRAW_SPEC = True
            continue
        if "scroll" not in _hint or "state" not in _hint:
            continue
        _frm = _anim.get("from") if isinstance(_anim, dict) else None
        _fo = _frm.get("opacity") if isinstance(_frm, dict) else None
        if SCROLL_FADE_FROM is None and isinstance(_fo, (int, float)) and not isinstance(_fo, bool) and 0 < _fo < 1:
            SCROLL_FADE_FROM = float(_fo)
except (OSError, json.JSONDecodeError):
    SCROLL_FADE_FROM = None
    STROKE_DRAW_SPEC = False

# P4 — remove THIS transpiler's previously-emitted components before regenerating
# so reused impl dirs do not accumulate stale/renamed orphans (e.g. _UncoveredText
# after it was split into _UncoveredHead / _UncoveredAfter*), which inflate the
# section count and risk duplicate/orphan content. Hand-written components (no
# auto-gen marker) are never touched.
if out_dir.is_dir():
    for _stale in out_dir.glob("*.tsx"):
        try:
            _head = _stale.read_text(encoding="utf-8", errors="replace")[:400]
        except OSError:
            continue
        if "Auto-generated" in _head and "scaffold-to-jsx" in _head:
            try:
                _stale.unlink()
            except OSError:
                pass

# Document-order index per node (depth-first, pre-order) — lets us place
# section-uncovered fragments at their real DOM position instead of dumping
# them all at the end of the page (loop-120 section-compare 0/14 regression).
DOC_ORDER = {}
def _index_doc_order(node, counter=[0]):
    if not isinstance(node, dict):
        return
    DOC_ORDER[id(node)] = counter[0]
    counter[0] += 1
    for c in node.get("children") or []:
        _index_doc_order(c)
def _inject_missing_images(struct, ref_dir):
    """P2 — images captured in visible-images.json but ABSENT from the DOM
    scaffold (lazy / IntersectionObserver galleries, e.g. realfood's 34 pyramid
    food images) would never be emitted. Inject each as an <img> into the
    section whose class matches its /images/<category>/ path, so the transpiler
    renders them and asset-download (which already harvests visible-images)
    serves them. No-op when visible-images.json is absent."""
    vi_path = ref_dir / "visible-images.json"
    if not vi_path.exists():
        return
    try:
        vi = json.loads(vi_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    items = vi if isinstance(vi, list) else (vi.get("images") or vi.get("items") or [])
    struct_text = json.dumps(struct)

    def _basename(u):
        return u.split("?", 1)[0].split("#", 1)[0].rstrip("/").split("/")[-1]

    def _category(u):
        m = re.search(r"/images/([^/]+)/", u)
        return m.group(1) if m else None

    by_cat, seen = {}, set()
    for it in items:
        src = it.get("src") if isinstance(it, dict) else (it if isinstance(it, str) else None)
        if not src or src in seen:
            continue
        seen.add(src)
        bn = _basename(src)
        if not bn or bn in struct_text:  # already present in the DOM scaffold
            continue
        cat = _category(src)
        if not cat:
            continue
        alt = it.get("alt", "") if isinstance(it, dict) else ""
        by_cat.setdefault(cat, []).append({"tag": "img", "src": src, "alt": alt or ""})
    if not by_cat:
        return

    def _find_container(cat):
        key = cat.replace("-", "").lower()
        found = [None]
        def w(n):
            if found[0] or not isinstance(n, dict):
                return
            if key in str(n.get("class", "")).replace("-", "").lower():
                found[0] = n
                return
            for c in n.get("children") or []:
                w(c)
        w(struct)
        return found[0]

    for cat, imgs in by_cat.items():
        cont = _find_container(cat)
        if cont is None:
            continue
        cont.setdefault("children", [])
        if not isinstance(cont["children"], list):
            cont["children"] = []
        cont["children"].extend(imgs)


_inject_missing_images(structure, Path(sys.argv[1]).parent)
_index_doc_order(structure)
_build_parent_map(structure)  # Fix 89 — must run after inject (inject may add children)

# Section list — prefer section-map.json; fall back to top-level children.
sec_path = Path(sys.argv[2])
sections = []
if sec_path.is_file():
    sm = json.loads(sec_path.read_text(encoding="utf-8"))
    sections = sm.get("sections", []) if isinstance(sm, dict) else (sm if isinstance(sm, list) else [])
if not sections:
    # Fallback: treat structure.json's direct children as sections.
    sections = [
        {"index": i, "tag": c.get("tag"), "cls": c.get("class", ""), "id": c.get("id")}
        for i, c in enumerate(structure.get("children", []) or [])
        if isinstance(c, dict) and c.get("tag") in ("section", "header", "footer", "main", "nav", "article")
    ]
if not sections:
    print("scaffold-to-jsx: no sections to transpile", file=sys.stderr)
    sys.exit(2)

# Fix 84 — ids claimed by section-map entries. A class-only entry must never
# consume a node carrying one of these ids (repeated CSS-module classes made
# the cta's class match steal the id=faqs node; the faqs entry then fell back
# to a 136px fragment — caught live by the geometry-sanity gate).
RESERVED_SECTION_IDS = {
    str(s.get("id")) for s in sections
    if isinstance(s, dict) and s.get("id")
}
# Fix 89 — first-token class of every section-map entry.  Used by the
# anonymous-wrapper promotion guard to detect whether the parent container
# also holds OTHER named sections (making it a multi-section root that must
# NOT be promoted wholesale).
SECTION_FIRST_CLASSES = {
    (s.get("cls") or s.get("className") or "").split()[0]
    for s in sections
    if isinstance(s, dict) and (s.get("cls") or s.get("className") or "").strip()
}

written = []
exports = []
# Parallel to the section entries appended to `exports`: the document-order key
# for each section component (so uncovered fragments can be interleaved at their
# real DOM position). None when the subtree wasn't located.
section_doc_keys = {}
reveal_sections = set()  # P3a — section component names whose subtree fades in on scroll
scrub_scale_sections = set()  # Fix 113 — sections containing a frozen scroll-zoom scale target
seen_names = {}  # Fix 15 — dedup component names so page.tsx imports are unique.
consumed = set()  # Fix 16b — id(node) of subtrees already assigned to a section.
for i, sec in enumerate(sections):
    base = section_component_name(sec, i)
    # If name already used, suffix with the section index to make it unique.
    if base in seen_names:
        seen_names[base] += 1
        name = f"{base}{seen_names[base]}"
    else:
        seen_names[base] = 1
        name = base
    subtree = find_subtree_for_section(structure, sec, consumed)
    # Section anchors — captures made before extract-dom recorded HTML ids have
    # no `id` on the subtree root, but section-map.json carries the section's id
    # (#problem, #pyramid, ...). Stamp it so the emitted section is addressable:
    # section-compare locates impl sections by id (11/14 scored MISSING without
    # it) and in-page anchor links (href="#problem") resolve.
    _sec_id = sec.get("id") if isinstance(sec, dict) else None
    if subtree is not None and _sec_id and not subtree.get("id"):
        subtree["id"] = _sec_id
    # Fix 88 — ancestor backdrop band: the ref wraps mid-page sections in a
    # FULL-WIDTH colored band the flat emission drops (screenshot-verified:
    # white headline invisible on cream; painting only the section column
    # leaves cream gutters because sections are narrower than the band). When
    # a bg-less section sits inside such a band, the emitted component is
    # wrapped in a styling-only full-bleed div carrying the band color;
    # consecutive band sections stack into a continuous band like the ref's
    # wrapper. The page-dominant fallback below is skipped when a band exists.
    _TRANSPARENT_BG = {"", "none", "rgba(0, 0, 0, 0)", "transparent"}
    _band_bg = None
    if subtree is not None:
        _sub_styles0 = subtree.get("styles") or {}
        if (_sub_styles0.get("background-color") or "") in _TRANSPARENT_BG:
            _band_bg = _nearest_ancestor_bg(structure, subtree)
    hover_rules = []  # Fix 19 — collected during render(); emitted as <style>.
    dominant_bg = sec.get("dominantBg") if isinstance(sec, dict) else None
    if _band_bg:
        dominant_bg = None  # the band is the backdrop; don't paint cream over it
    if subtree is not None and dominant_bg:
        sub_styles = subtree.get("styles") or {}
        existing = sub_styles.get("background-color", "")
        # Promote only when nothing meaningful is set. We treat transparent
        # rgba, empty, and the literal "none" as "missing" so we don't
        # clobber a hex/named color the page actually authored.
        TRANSPARENT = {"", "none", "rgba(0, 0, 0, 0)", "transparent"}
        if existing in TRANSPARENT:
            sub_styles["background-color"] = dominant_bg
            subtree["styles"] = sub_styles
    _rev0 = REVEAL_RESETS[0]
    _scrub0 = SCRUB_SCALE_STAMPED[0]
    if subtree is None:
        # Couldn't locate the subtree — emit a stub that imports the section
        # placeholder. Phase-5b visual-judge will surface this gap.
        body = f'  <section data-scaffold-warn="subtree-not-found-for-{name}" />'
    else:
        body = render(subtree, indent=2, hover_rules=hover_rules)
    def _subtree_has_sticky(n):
        if not isinstance(n, dict):
            return False
        if (n.get("styles") or {}).get("position") == "sticky":
            return True
        return any(_subtree_has_sticky(c) for c in n.get("children") or [])

    _has_sticky = subtree is not None and _subtree_has_sticky(subtree)
    if REVEAL_RESETS[0] > _rev0 and not _has_sticky:
        # This section contains real scroll/load reveals — wrap it in
        # <ScrollReveal> (P3a). Static sections (banner/nav) reset nothing and
        # are left un-wrapped. Sections containing ANY position:sticky element
        # (root OR descendant) are excluded: a ScrollReveal wrapper applies a
        # transform, which creates a containing block and breaks the sticky
        # pin (Fix 25/26) on every descendant.
        reveal_sections.add(name)
    # Fix 113 — this section carries a frozen scroll-zoom scale target (stamped
    # data-scroll-scrub-target); auto-wrap it in <ScrollScrub scale=…> at the
    # entry so the #3 zoom is DETERMINISTIC (no agent guessing / host ping-pong).
    # Same sticky exclusion as reveals (the scale wrapper is a transform → would
    # break a sticky descendant's pin).
    if SCRUB_SCALE_STAMPED[0] > _scrub0 and not _has_sticky and SCRUB_WRAP_ATTRS:
        scrub_scale_sections.add(name)
    # Fix 19 — build a <style> block from any collected :hover rules so the
    # captured transition (Fix 16) actually has a target to animate to.
    hover_css = ""
    if hover_rules:
        css_parts = []
        for hov_id, decls in hover_rules:
            decl_text = "; ".join(f"{k}: {v}" for k, v in decls.items())
            css_parts.append(f".{hov_id}:hover {{ {decl_text} }}")
        hover_css = "\n".join(css_parts)
    style_block = ""
    if hover_css:
        # Escape backticks + ${ for JS template literal safety.
        css_safe = hover_css.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        style_block = (
            "        <style dangerouslySetInnerHTML={{ __html: `"
            f"{css_safe}"
            "` }} />\n"
        )
    if style_block:
        wrapped_body = (
            "    <>\n"
            f"{style_block}"
            f"{body}\n"
            "    </>"
        )
    else:
        wrapped_body = body
    # Fix 26 — if this section's root is position:sticky, wrap the component in
    # its captured relative containing-block ancestor (with that ancestor's
    # height as a min-height floor) so the sticky pins then RELEASES at the
    # section end instead of pinning to the page body for the whole scroll.
    if subtree is not None and (subtree.get("styles") or {}).get("position") == "sticky":
        anc = _find_relative_ancestor(structure, subtree)
        if anc is not None:
            anc_tag = (anc.get("tag") or "div").lower()
            anc_cls = safe_class_name(anc.get("class", ""))
            anc_style = {"position": "relative"}
            anc_styles = anc.get("styles") or {}
            anc_h = anc_styles.get("height")
            if anc_h:
                anc_style["min-height"] = _effective_flow_height(anc_h, anc_styles)
            anc_cls_attr = f' className="{anc_cls}"' if anc_cls else ""
            wrapped_body = (
                f"    <{anc_tag}{anc_cls_attr} style={style_to_jsx(anc_style)}>\n"
                f"{wrapped_body}\n"
                f"    </{anc_tag}>"
            )
    # Fix 88 — full-bleed band wrapper (outermost): the ref's band wrapper is
    # full-width while the section is a narrower column; the band div paints
    # the backdrop across the bleed so columns sit ON the band like the ref.
    # Fix 97 (#1) — break the band out to the full viewport width even when an
    # ancestor (the P5-reflowed root) carries a max-width: a centered root would
    # otherwise leave side gutters where the dark/cream backdrop should reach the
    # screen edge. The calc(50% - 50vw) negative margins are the standard
    # full-bleed breakout; the existing overflow-x:clip on root prevents any
    # horizontal scroll. Content columns inside keep their own centering.
    if _band_bg:
        wrapped_body = (
            f'    <div style={{{{ backgroundColor: "{_band_bg}", '
            f'width: "100vw", marginLeft: "calc(50% - 50vw)", '
            f'marginRight: "calc(50% - 50vw)" }}}}>\n'
            f"{wrapped_body}\n"
            f"    </div>"
        )
    file_body = (
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh (Fix 13/18/19).\n"
        "// DO NOT hand-edit at the JSX level — re-run the transpiler if the ref changes.\n"
        "// Event handlers / state / scroll-trigger animations can be wired in a wrapper.\n"
        "\n"
        f"export default function {name}() {{\n"
        f"  return (\n"
        f"{wrapped_body}\n"
        f"  );\n"
        f"}}\n"
    )
    out_path = out_dir / f"{name}.tsx"
    out_path.write_text(file_body, encoding="utf-8")
    written.append(out_path.name)
    exports.append(name)
    section_doc_keys[name] = DOC_ORDER.get(id(subtree)) if subtree is not None else None

# Catch-all: nodes no section component rendered (header/nav buttons, footer
# credits, mid-page blocks a mis-resolved section subtree missed) are emitted
# into per-position _UncoveredHead / _UncoveredAfter<i> components so no visible
# ref content is dropped OR misplaced — see the document-position emission below.
_uncovered = []


def _has_rendered_descendant(node):
    if not isinstance(node, dict):
        return False
    if id(node) in RENDERED_IDS:
        return True
    return any(_has_rendered_descendant(c) for c in node.get("children") or [])


def _collect_uncovered(node):
    if not isinstance(node, dict):
        return
    if (node.get("tag") or "").lower() in SKIP_TAGS:
        return
    if id(node) in RENDERED_IDS:
        return  # node and its subtree were already emitted
    # Only render the whole node when NOTHING in its subtree was already
    # rendered — otherwise render() would re-emit that content (duplicate).
    if not _has_rendered_descendant(node):
        t = node.get("text")
        njson = json.dumps(node, ensure_ascii=False)
        has_content = (
            (isinstance(t, str) and t.strip())
            or '"text"' in njson
            or '"src"' in njson or '"srcset"' in njson  # asset nodes (img/video/source)
            or (node.get("tag") or "").lower() in (
                "img", "svg", "video", "source", "picture", "use", "image")
        )
        if has_content:
            _uncovered.append(node)
            return  # render() on this node includes its whole (unrendered) subtree
    # Mixed subtree: descend to collect only the genuinely-unrendered branches.
    for c in node.get("children") or []:
        _collect_uncovered(c)


_collect_uncovered(structure)
if _uncovered:
    # Place each uncovered fragment at its real document position instead of
    # dumping them all in one component at the end of the page. Group fragments
    # by the section they follow so a mid-page block (e.g. the food-pyramid
    # category cards) renders inside its section run, not at the page bottom.
    section_names = list(exports)  # only section components so far
    # Forward-fill section doc keys so a section whose subtree wasn't located
    # inherits its predecessor's position (keeps the sequence monotonic).
    ff = []
    last = -1
    for nm in section_names:
        k = section_doc_keys.get(nm)
        if k is None:
            k = last
        ff.append(k)
        last = k

    def _insert_index(order):
        """Largest section index whose doc key <= order; -1 => before all."""
        idx = -1
        for i, k in enumerate(ff):
            if k <= order:
                idx = i
            else:
                break
        return idx

    groups = {}  # insert_index -> [nodes] (document order preserved)
    group_seq = []  # insert_index order of first appearance
    for n in _uncovered:
        o = DOC_ORDER.get(id(n), 1 << 30)
        gi = _insert_index(o)
        if gi not in groups:
            groups[gi] = []
            group_seq.append(gi)
        groups[gi].append(n)

    group_comp = {}
    for gi in group_seq:
        parts = [r for r in (render(node, indent=3) for node in groups[gi]) if r]
        if not parts:
            continue
        cname = "_UncoveredHead" if gi < 0 else f"_UncoveredAfter{gi}"
        body = "\n".join(parts)
        file_body = (
            "// Auto-generated by scaffold-to-jsx.sh — section-uncovered ref nodes,\n"
            "// preserved at their document position so no visible content is dropped\n"
            "// or misplaced (loop-120 section-compare regression fix).\n"
            f"export default function {cname}() {{\n"
            "  return (\n"
            '    <section data-uncovered="text">\n'
            f"{body}\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        )
        (out_dir / f"{cname}.tsx").write_text(file_body, encoding="utf-8")
        written.append(f"{cname}.tsx")
        group_comp[gi] = cname

    # Rebuild the App body order: head group, then each section followed by the
    # uncovered fragments that belong right after it.
    new_exports = []
    if -1 in group_comp:
        new_exports.append(group_comp[-1])
    for i, nm in enumerate(section_names):
        new_exports.append(nm)
        if i in group_comp:
            new_exports.append(group_comp[i])
    exports = new_exports

# Also emit a barrel index so page.tsx can `import * as Sections from "./components"`.
index_body = "\n".join(f'export {{ default as {n} }} from "./{n}";' for n in exports) + "\n"
(out_dir / "index.ts").write_text(index_body, encoding="utf-8")

# Universality audit CRITICAL: prior version always
# emitted Next App Router `app/page.tsx`, coercing Vite/Astro/SvelteKit/
# Remix/Parcel impls into the wrong entry shape. Detect impl stack
# from package.json + characteristic config files, then emit the
# stack-appropriate entry.
#
# Layout assumption: out_dir is impl/src/components, so impl root is
# out_dir.parent.parent.
impl_root = out_dir.parent.parent
pkg_json_path = impl_root / "package.json"
pkg_deps: dict[str, str] = {}
pkg_scripts: dict[str, str] = {}
if pkg_json_path.is_file():
    try:
        pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            d = pkg_data.get(key) or {}
            if isinstance(d, dict):
                pkg_deps.update({k: str(v) for k, v in d.items()})
        scripts = pkg_data.get("scripts") or {}
        if isinstance(scripts, dict):
            pkg_scripts.update({k: str(v) for k, v in scripts.items()})
    except (OSError, ValueError):
        pass


def _detect_stack() -> str:
    has_next = "next" in pkg_deps
    has_vite = "vite" in pkg_deps or "@vitejs/plugin-react" in pkg_deps
    has_remix = "@remix-run/react" in pkg_deps or "@remix-run/node" in pkg_deps
    has_astro = "astro" in pkg_deps
    has_sveltekit = "@sveltejs/kit" in pkg_deps
    next_eff = has_next and any(
        (impl_root / cf).is_file()
        for cf in ("next.config.ts", "next.config.js", "next.config.mjs")
    ) or "next" in (pkg_scripts.get("dev") or pkg_scripts.get("build") or "")
    vite_eff = has_vite and any(
        (impl_root / cf).is_file()
        for cf in ("vite.config.ts", "vite.config.js", "vite.config.mjs")
    ) or "vite" in (pkg_scripts.get("dev") or pkg_scripts.get("build") or "")
    if has_remix:
        return "remix"
    if has_astro:
        return "astro"
    if has_sveltekit:
        return "sveltekit"
    if next_eff:
        return "next"
    if vite_eff:
        return "vite"
    if has_next:
        return "next"
    if has_vite:
        return "vite"
    return "vite"  # safest default — React+Tailwind+Vite is the
                  # plugin's documented default scaffold


stack = _detect_stack()

# Root element from structure.json (typically <main> or <body>).
root_tag = (structure.get("tag") or "main").lower()
root_cls = safe_class_name(structure.get("class") or "")
root_styles = dict(structure.get("styles") or {})
# Fix 75 — never bake the captured page height onto the root. The root/body
# height is DERIVED from content at capture time (e.g. 20133px): baking it
# (a) freezes a stale page length — docH stays pinned regardless of what the
# sections actually render to — and (b) becomes the resolution base for
# ref-CSS `height:100%` descendants, ballooning them to the full page height
# (loop-128/129: a footer grew to 20133px). Content sizes the root; the
# body→div branch below restores the viewport floor (min-height:100vh).
for _hk in ("height", "min-height"):
    _hv = root_styles.get(_hk)
    if isinstance(_hv, str) and _hv.strip().endswith("px"):
        root_styles.pop(_hk, None)
# P1 — a captured <body>/<html> cannot be re-emitted as a nested <body> inside
# the mount point (#root): it is invalid HTML and the page base background may
# not paint the full viewport, so a dark section appears to "leak" onto the
# page root. Render it as a viewport-filling <div> that carries the ref body's
# background, making the page base the ref body color (e.g. cream).
if root_tag in ("body", "html"):
    root_tag = "div"
    root_styles.setdefault("min-height", "100vh")


def _dominant_descendant_bg(node):
    """Most common SOLID background-color among content nodes (excludes the root
    itself and translucent rgba overlays). The real resting page bg."""
    from collections import Counter

    _SKIP = {"", "transparent", "rgba(0, 0, 0, 0)", "rgba(0,0,0,0)"}
    counts: Counter = Counter()

    def walk(n, is_root):
        if not isinstance(n, dict):
            return
        if not is_root:
            bg = (n.get("styles") or {}).get("background-color", "")
            bg = bg.strip() if isinstance(bg, str) else ""
            if bg and bg not in _SKIP and not bg.startswith("rgba"):
                counts[bg] += 1
        for c in n.get("children") or []:
            walk(c, False)

    walk(node, True)
    return counts.most_common(1)[0][0] if counts else None


# R1b — when the captured root background-color EQUALS the root text color, the
# body was captured in an unrevealed intro state (text painted invisibly on the
# pre-animation dark backdrop, e.g. realfood's rgb(17,0,0) intro that transitions
# to cream). That is never the resting page background, so propagating it (Fix
# 56) paints the whole page dark. Substitute the dominant solid content
# background-color (the real page bg, e.g. cream) for both the root div and the
# global html,body override below.
_root_color = (root_styles.get("color") or "").strip()
_root_bg_raw = (root_styles.get("background-color") or "").strip()
if _root_bg_raw and _root_bg_raw == _root_color:
    _dom_bg = _dominant_descendant_bg(structure)
    if _dom_bg and _dom_bg != _root_bg_raw:
        root_styles["background-color"] = _dom_bg
# P5 — the root carries the desktop capture width (e.g. 1440px); make it
# viewport-relative so the whole page reflows at narrow widths and centres.
_rw = root_styles.get("width", "")
if isinstance(_rw, str) and _rw.endswith("px"):
    try:
        _rwpx = float(_rw[:-2])
    except ValueError:
        _rwpx = 0.0
    if _rwpx >= REFLOW_ROOT_MIN_PX:
        root_styles.pop("width", None)
        root_styles["max-width"] = _rw
        root_styles["width"] = "100%"
        root_styles.setdefault("margin-left", "auto")
        root_styles.setdefault("margin-right", "auto")
root_cls_attr = f' className="{root_cls}"' if root_cls else ""
root_style_attr = f" style={style_to_jsx(root_styles)}" if root_styles else ""

# R1 — imported ref CSS chunks can leave html/body with a dark background (a
# late `body{background-color:inherit}` inherits the dark html bg), so the page
# base shows dark in margins / overscroll even behind the cream root div. Emit a
# global html,body override = the ref body background so the base is cream
# everywhere. !important beats the ungrounded ref `body{}` rules.
# R3 — JS-positioned elements (e.g. pyramid foods at left up to ~1656px) extend
# the body well past the viewport. The ref clips this with html{overflow-x:clip};
# guarantee it so the clone's body scrollWidth stays <= viewport regardless of
# the imported CSS.
_root_bg = (root_styles.get("background-color") or "").strip()
GLOBAL_STYLE = ""
_global_decls = "overflow-x:clip;max-width:100vw;"
if _root_bg:
    _global_decls = f"background-color:{_root_bg} !important;" + _global_decls
_css = f"html,body{{{_global_decls}}}"
_css_safe = _css.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
GLOBAL_STYLE = (
    "      <style dangerouslySetInnerHTML={{ __html: `" + _css_safe + "` }} />\n"
)
# Also clip on the root div itself (belt-and-suspenders for the viewport-filling
# container that holds the overflowing sections). Recompute the style attr since
# root_styles changed.
root_styles.setdefault("overflow-x", "clip")
root_style_attr = f" style={style_to_jsx(root_styles)}" if root_styles else ""

# Sections in section-map order — they're already ordered by `top` upstream.
# P3a — wrap sections that contain real scroll/load opacity reveals in
# <ScrollReveal> so the emitted helper is actually used (not dead code). Only
# reveal sections are wrapped; static sections (banner/nav) are left as-is.
_WRAP_REVEAL = SCROLL_DRIVEN_REQUIRED and bool(reveal_sections)


def _section_ref(n):
    inner = f"<{n} />"
    if _WRAP_REVEAL and n in reveal_sections:
        inner = f"<ScrollReveal>{inner}</ScrollReveal>"
    # Fix 113 — deterministic scroll-zoom: wrap the scrub-scale section in
    # <ScrollScrub scale=…> (outermost) so #3 reproduces without the agent.
    if SCRUB_WRAP_ATTRS and n in scrub_scale_sections:
        inner = f"<ScrollScrub {SCRUB_WRAP_ATTRS}>{inner}</ScrollScrub>"
    return f"      {inner}"


section_jsx = GLOBAL_STYLE + "\n".join(_section_ref(n) for n in exports)


def _emit_next_page() -> Path:
    page_dir = out_dir.parent / "app"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "page.tsx"
    imports = "\n".join(
        f'import {n} from "@/components/{n}";' for n in exports
    )
    if _WRAP_REVEAL:
        imports += '\nimport ScrollReveal from "@/lib/ScrollReveal";'
    if scrub_scale_sections:  # Fix 113 — deterministic #3 zoom auto-wrap
        imports += '\nimport ScrollScrub from "@/lib/ScrollScrub";'
    driver_line = ""
    if SCROLL_FADE_STAMPED[0] or STROKE_DRAW_STAMPED[0]:
        # Fix 74/76 — mount the state driver so stamped fade/draw-in elements
        # animate. Was Vite-only; the Next page omitted it, leaving every stamped
        # element inert (stuck at its captured inactive state) on the Next stack.
        imports += '\nimport ScrollStateDriver from "@/lib/ScrollStateDriver";'
        driver_line = "      <ScrollStateDriver />\n"
    body = (
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh.\n"
        "// DO NOT hand-edit — re-run the transpiler if the ref changes.\n"
        "// stack: next App Router\n"
        "\n"
        f"{imports}\n"
        "\n"
        "export default function Page() {\n"
        "  return (\n"
        f"    <{root_tag}{root_cls_attr}{root_style_attr}>\n"
        f"{driver_line}{section_jsx}\n"
        f"    </{root_tag}>\n"
        "  );\n"
        "}\n"
    )
    page_path.write_text(body, encoding="utf-8")
    return page_path


def _emit_vite_entry() -> Path:
    # Vite+React: emit src/App.tsx wrapping the components. main.tsx
    # is typically already written by `npm create vite` and renders
    # <App />; we don't overwrite main.tsx, only App.tsx.
    app_path = out_dir.parent / "App.tsx"
    imports = "\n".join(
        f"import {n} from './components/{n}';" for n in exports
    )
    open_wrap, close_wrap = "", ""
    if SMOOTH_SCROLL_REQUIRED:
        imports += "\nimport SmoothScroll from './lib/SmoothScroll';"
        open_wrap = "      <SmoothScroll>\n"
        close_wrap = "\n      </SmoothScroll>"
    if _WRAP_REVEAL:
        imports += "\nimport ScrollReveal from './lib/ScrollReveal';"
    if scrub_scale_sections:  # Fix 113 — deterministic #3 zoom auto-wrap
        imports += "\nimport ScrollScrub from './lib/ScrollScrub';"
    driver_line = ""
    if SCROLL_FADE_STAMPED[0] or STROKE_DRAW_STAMPED[0]:
        # Fix 74/76 — mount the state driver once so stamped elements animate.
        imports += "\nimport ScrollStateDriver from './lib/ScrollStateDriver';"
        driver_line = "      <ScrollStateDriver />\n"
    body = (
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh.\n"
        "// DO NOT hand-edit — re-run the transpiler if the ref changes.\n"
        "// stack: vite + react\n"
        "\n"
        f"{imports}\n"
        "\n"
        "export default function App() {\n"
        "  return (\n"
        f"    <{root_tag}{root_cls_attr}{root_style_attr}>\n"
        f"{open_wrap}{driver_line}{section_jsx}{close_wrap}\n"
        f"    </{root_tag}>\n"
        "  );\n"
        "}\n"
    )
    app_path.write_text(body, encoding="utf-8")
    return app_path


def _emit_remix_root() -> Path:
    page_dir = impl_root / "app"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "_index.tsx"
    try:
        rel = Path("../") / out_dir.relative_to(impl_root)
    except ValueError:
        rel = out_dir
    imports = "\n".join(
        f"import {n} from '{rel.as_posix()}/{n}';" for n in exports
    )
    body = (
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh.\n"
        "// stack: remix\n"
        "\n"
        f"{imports}\n"
        "\n"
        "export default function Index() {\n"
        "  return (\n"
        f"    <{root_tag}{root_cls_attr}{root_style_attr}>\n"
        f"{section_jsx}\n"
        f"    </{root_tag}>\n"
        "  );\n"
        "}\n"
    )
    page_path.write_text(body, encoding="utf-8")
    return page_path


def _emit_astro_index() -> Path:
    page_dir = impl_root / "src" / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "index.astro"
    imports = "\n".join(
        f"import {n} from '../components/{n}.tsx';" for n in exports
    )
    children = "\n".join(f"  <{n} client:load />" for n in exports)
    body = (
        "---\n"
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh.\n"
        "// stack: astro\n"
        f"{imports}\n"
        "---\n"
        f"<{root_tag}>\n"
        f"{children}\n"
        f"</{root_tag}>\n"
    )
    page_path.write_text(body, encoding="utf-8")
    return page_path


def _emit_sveltekit_route() -> Path:
    page_dir = impl_root / "src" / "routes"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "+page.svelte"
    try:
        rel_to_route = Path("../../") / out_dir.relative_to(impl_root)
    except ValueError:
        rel_to_route = out_dir
    imports = "\n".join(
        f"  import {n} from '{rel_to_route.as_posix()}/{n}.tsx';"
        for n in exports
    )
    children = "\n".join(f"  <{n} />" for n in exports)
    body = (
        "<!-- Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh -->\n"
        "<!-- stack: sveltekit -->\n"
        "<script lang=\"ts\">\n"
        f"{imports}\n"
        "</script>\n"
        "\n"
        f"<{root_tag}>\n"
        f"{children}\n"
        f"</{root_tag}>\n"
    )
    page_path.write_text(body, encoding="utf-8")
    return page_path


EMITTERS = {
    "next": _emit_next_page,
    "vite": _emit_vite_entry,
    "remix": _emit_remix_root,
    "astro": _emit_astro_index,
    "sveltekit": _emit_sveltekit_route,
}

emitter = EMITTERS.get(stack, _emit_vite_entry)
page_path = emitter()

print(f"scaffold-to-jsx: wrote {len(written)} components to {out_dir}")
for name in written[:6]:
    print(f"  - {name}")
if len(written) > 6:
    print(f"  ... +{len(written) - 6} more")
print(f"scaffold-to-jsx: stack={stack} → wrote entry at {page_path}")
print(f"  root tag: <{root_tag}>, imports: {len(exports)} sections")

# scaffold-residue (Fix 71) — a regen replaces the previously-wired components,
# which can sever references to HAND-WRITTEN helper modules under src/ (agent
# enhancement libs). Their now-dead PascalCase exports trip the scaffold-residue
# gate (>=3 orphans = fail). Machine-owned files are rewired by this very regen;
# hand-written modules cannot be rewired deterministically, so fully-unreferenced
# AND un-imported ones are relocated to impl/attic/ (outside the residue
# scanner's src/ scope, preserved for the agent to re-wire). Anything imported
# or rendered anywhere stays untouched; entry files are never candidates.
_src_root = out_dir.parent
_impl_root = _src_root.parent
_EXPORT_RE = re.compile(r"export\s+(?:default\s+)?(?:function|const)\s+([A-Z][A-Za-z0-9_]*)")
_blobs = {}
for _p in list(_src_root.rglob("*.tsx")) + list(_src_root.rglob("*.jsx")):
    try:
        _blobs[_p] = _p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _blobs[_p] = ""
for _p, _txt in list(_blobs.items()):
    if _p.stem.lower() in ("main", "app", "index"):
        continue
    # Fix 51 contract: hand-written files under the component out_dir are never
    # touched by the regen — only helper modules outside it (src/lib etc.) are
    # attic candidates.
    if out_dir in _p.parents:
        continue
    _head = _txt[:400]
    if "Auto-generated" in _head:
        continue  # machine-owned (scaffold/emit-scroll-helpers) — wired by this regen
    _names = set(_EXPORT_RE.findall(_txt))
    if not _names:
        continue
    _used = False
    for _q, _qt in _blobs.items():
        if _q == _p:
            continue
        if re.search(r"from\s+['\"][^'\"]*/" + re.escape(_p.stem) + r"['\"]", _qt):
            _used = True
            break
        if any(re.search(r"<" + _n + r"[\s/>]", _qt) or ("createElement(" + _n) in _qt for _n in _names):
            _used = True
            break
    if _used:
        continue
    _rel = _p.relative_to(_src_root)
    _dest = _impl_root / "attic" / _rel
    _dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _p.rename(_dest)
        del _blobs[_p]
        print(f"scaffold-to-jsx: atticized unreferenced hand-written module {_rel}")
    except OSError:
        pass

# Proof-of-run stamp so the generation flow / telemetry can confirm the
# deterministic base was produced (text completeness comes from here, not
# hand transcription).
import hashlib as _hashlib
_ref_dir = Path(sys.argv[1]).parent
_struct_sha = _hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
_stamp = {
    "schemaVersion": 1,
    "producer": "skills/visual-debug/scripts/scaffold-to-jsx.sh",
    "structureSha256": _struct_sha,
    "componentsWritten": len(written),
    "components": written,
    "outDir": str(out_dir),
    "stack": stack,
}
(_ref_dir / "scaffold-base-stamp.json").write_text(
    json.dumps(_stamp, indent=2) + "\n", encoding="utf-8",
)
print(f"scaffold-to-jsx: stamp → {_ref_dir / 'scaffold-base-stamp.json'}")
PY

# Emit deterministic scroll helpers (src/lib/SmoothScroll.tsx, ScrollReveal.tsx)
# from generation-plan.json when it requires them. Self-skips when the plan is
# absent or does not require smooth scroll / scrollDriven, so transpiler runs
# without a plan are unaffected.
if [[ -f "$REF_DIR/generation-plan.json" ]]; then
  bash "$SCRIPT_DIR/emit-scroll-helpers.sh" "$REF_DIR" "$IMPL_DIR"
fi

# R2 — download referenced assets into impl/public so a REGENERATED impl is
# self-contained. A fresh impl emits correct <img> src (Fix 33) but, if the
# build orchestration downloaded to a prior impl dir, has no files → every image
# 404s (loop-123: 0/49 loaded). Idempotent (skips existing), non-fatal, and
# self-skips when visible-images.json is absent (so plan-less/unit runs are
# unaffected). Resolve asset-download.sh from the repo root above the skill dir.
_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." 2>/dev/null && pwd || true)"
_ASSET_DL="${_REPO_ROOT:-}/scripts/extract/asset-download.sh"
if [[ -f "$REF_DIR/visible-images.json" && -f "$_ASSET_DL" ]]; then
  bash "$_ASSET_DL" "$REF_DIR" "$IMPL_DIR/public" >/dev/null 2>&1 || true
fi
