"""Phase-2 extraction artifact finalization helpers.

The deterministic Phase 2 and manual fast paths can produce enough source
artifacts to prove an absence (no inline SVGs, no CSS custom properties) or to
summarize reusable style evidence, while still leaving the canonical handoff
files empty or missing. This module turns those source artifacts into explicit
sentinels/summaries so gates distinguish "observed none" from "skipped".
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

_JSON = dict[str, Any]
_NOISE = {
    "",
    "normal",
    "none",
    "auto",
    "0px",
    "rgba(0, 0, 0, 0)",
    "all 0s ease 0s",
    "all",
    "0s",
    "ease",
}
_CSS_VAR_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+)")
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)

_KNOWN_PSEUDO_RE = re.compile(
    r"^[A-Za-z0-9_\-\.\#\[\]\*]*:(?:hover|focus|active|visited|link|checked|"
    r"disabled|enabled|empty|target|root|is|where|not|has|nth|first|last|"
    r"only|before|after|placeholder|focus-visible|focus-within)"
)
_DECLARATION_SHAPE_RE = re.compile(r"^[A-Za-z-]+\s*:\s*\S")


def _is_valid_selector(selector: object) -> bool:
    """True when `selector` looks like a CSS selector, not a declaration
    fragment. The flat _CSS_RULE_RE on nested CSS emitted fragments like
    'transform .2s ease;&' as selectors (the auto-minted spec's garbage
    target came from exactly that); every spec/hover consumer must filter
    targets through this before emitting them."""
    s = " ".join(str(selector or "").split())
    if not s or len(s) > 400:
        return False
    if any(c in s for c in "{};&"):
        return False
    if _DECLARATION_SHAPE_RE.match(s) and not _KNOWN_PSEUDO_RE.match(s):
        return False
    return True


def _combine_nested_selector(parent: str, child: str) -> str:
    child = " ".join(child.split())
    if not parent:
        return child
    # Cross-product over comma groups on BOTH sides (Codex review: replacing
    # '&' with the whole parent group turned `.a, .b { &:hover }` into
    # `.a, .b:hover`, losing `.a:hover`).
    parents = [p.strip() for p in parent.split(",") if p.strip()]
    parts = []
    for c in child.split(","):
        c = c.strip()
        if not c:
            continue
        for par in parents:
            parts.append(c.replace("&", par) if "&" in c else f"{par} {c}")
    return ", ".join(parts)


def _iter_css_rules(css_text: str) -> list[tuple[str, str]]:
    """Brace-depth CSS rule walker that resolves CSS nesting.

    Yields (selector_group, declarations) for every leaf rule. The flat
    _CSS_RULE_RE cannot see nesting: after an inner block closes, the text
    between '}' and the next '{' is the TAIL of the parent's declarations,
    which the regex then emits as a 'selector'. This walker keeps a selector
    stack instead, resolves '&' against the parent, and treats at-rule
    preludes (@media, @supports, ...) as transparent wrappers."""
    out: list[tuple[str, str]] = []
    stack: list[str] = []
    decls: list[list[str]] = [[]]
    buf: list[str] = []
    for ch in css_text:
        if ch == "{":
            stack.append("".join(buf).strip())
            decls.append([])
            buf = []
        elif ch == "}":
            tail = "".join(buf).strip()
            buf = []
            if not stack:
                continue
            prelude = stack.pop()
            own = decls.pop() if len(decls) > 1 else []
            if tail:
                own.append(tail)
            decl_text = "; ".join(d.strip().rstrip(";") for d in own if d.strip())
            if decl_text and prelude and not prelude.startswith("@"):
                parent = ""
                for level in stack:
                    if level and not level.startswith("@"):
                        parent = _combine_nested_selector(parent, level)
                resolved = _combine_nested_selector(parent, prelude)
                if resolved:
                    out.append((resolved, decl_text))
        elif ch == ";":
            decls[-1].append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    return out
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_BODY_STATE_SELECTOR_RE = re.compile(r"\b(?:body|html)(?:\.[A-Za-z0-9_-]+|\[[^\]]+\])")

_BUNDLE_PROPS: dict[str, tuple[str, ...]] = {
    "surface": ("background-color", "background-image", "border", "box-shadow"),
    "shape": ("border-radius", "padding"),
    "type": ("font-size", "font-weight", "font-family", "line-height", "letter-spacing"),
    "tone": ("color", "background-color", "border-color"),
    "motion": (
        "transition",
        "transition-duration",
        "transition-timing-function",
        "animation",
        "animation-duration",
        "animation-timing-function",
    ),
}


def finalize_extraction_artifacts(ref_dir: Path) -> dict[str, str]:
    """Write canonical Phase-2 sentinel/summary artifacts when source evidence exists.

    Existing non-empty real artifacts are preserved. Empty or missing handoff
    files are rewritten only when they can be derived from canonical source
    artifacts already in ``ref_dir`` (``structure.json`` and downloaded
    ``css/*.css``). The return value maps artifact names to actions for tests
    and diagnostics.
    """
    ref_dir = Path(ref_dir)
    actions: dict[str, str] = {}
    structure = _load_structure(ref_dir / "structure.json")

    if structure is not None:
        _finalize_inline_svgs(ref_dir, structure, actions)
        _finalize_body_state(ref_dir, structure, actions)
        _finalize_design_bundles(ref_dir, structure, actions)
    _finalize_css_variables(ref_dir, actions)
    return actions


def _load_structure(path: Path) -> Any | None:
    data = _load_json(path)
    if isinstance(data, dict | list):
        return data
    return None


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    # Some captured evals write JSON.stringify(payload) to a JSON file, leaving
    # a double-encoded string such as "[]". Unwrap once so sentinels can
    # recognize the underlying observation.
    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return data
    return data


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _walk_nodes(node: Any) -> list[_JSON]:
    out: list[_JSON] = []

    def walk(current: Any) -> None:
        if isinstance(current, dict):
            out.append(current)
            for child in current.get("children") or []:
                walk(child)
        elif isinstance(current, list):
            for child in current:
                walk(child)

    walk(node)
    return out


def _tag(node: _JSON) -> str:
    raw = node.get("tag")
    return raw.lower() if isinstance(raw, str) else ""


def _first_class(node: _JSON) -> str:
    raw = node.get("class") or node.get("className") or ""
    if not isinstance(raw, str):
        return ""
    return raw.strip().split()[0] if raw.strip() else ""


def _styles(node: _JSON) -> _JSON:
    raw = node.get("styles")
    return raw if isinstance(raw, dict) else {}


def _is_empty_file(path: Path) -> bool:
    try:
        return (not path.exists()) or path.stat().st_size < 10
    except OSError:
        return True


def _svg_payload_has_entries(data: Any) -> bool:
    if isinstance(data, list):
        return bool(data)
    if isinstance(data, dict):
        for key in ("svgs", "inlineSvgs", "items"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return True
        count = data.get("svgCount")
        return isinstance(count, int) and count > 0
    return False


_SVG_BOOKKEEPING_KEYS = frozenset(
    {"tag", "children", "styles", "display", "position", "svg", "text", "className"}
)
_SVG_SHAPE_TAGS = frozenset(
    {"path", "rect", "circle", "line", "polygon", "ellipse", "polyline"}
)
_SVG_LANDMARK_TAGS = frozenset({"section", "header", "footer", "nav"})


def _xml_attr_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def _xml_text_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _svg_node_to_markup(node: Any) -> str:
    """Reconstruct verbatim SVG markup from a captured structure.json node.

    ``extract-dom.sh`` records every SVG attribute generically (skipping only
    ``on*`` handlers) plus a small set of computed-style bookkeeping keys, so a
    faithful ``outerHTML`` can be rebuilt offline without a live browser.
    """
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag")
    if not isinstance(tag, str) or not tag:
        return ""
    attrs: list[str] = []
    for key, value in node.items():
        if key in _SVG_BOOKKEEPING_KEYS:
            continue
        if not isinstance(value, str):
            continue
        attrs.append(f'{key}="{_xml_attr_escape(value)}"')
    open_tag = tag if not attrs else tag + " " + " ".join(attrs)
    inner: list[str] = []
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        inner.append(_xml_text_escape(text.strip()))
    for child in node.get("children") or []:
        inner.append(_svg_node_to_markup(child))
    body = "".join(part for part in inner if part)
    if not body:
        return f"<{open_tag}/>"
    return f"<{open_tag}>{body}</{tag}>"


def _svg_dimension(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
        if match:
            return int(float(match.group(1)))
    return None


def _derive_inline_svgs(structure: Any) -> list[_JSON]:
    """Build inline-svgs entries (live-eval schema) from structure.json offline.

    Mirrors the manual agent-browser eval in ``asset-extraction.md`` — each
    entry carries the verbatim ``outerHTML`` the generation step copies. Role
    classification is best-effort here (no bounding rects / ancestor links are
    available offline), and section is the nearest captured landmark class.
    """
    entries: list[_JSON] = []

    def shape_count(node: _JSON) -> int:
        return sum(1 for n in _walk_nodes(node) if _tag(n) in _SVG_SHAPE_TAGS)

    def visit(node: Any, section: str) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, section)
            return
        if not isinstance(node, dict):
            return
        tag = _tag(node)
        if tag in _SVG_LANDMARK_TAGS:
            section = _first_class(node) or tag
        if tag == "svg":
            first = _first_class(node)
            token = re.sub(r"[^a-zA-Z0-9_-]", "", first)
            selector = f"svg.{token}" if token else "svg"
            aria_raw = node.get("aria-label")
            aria = (
                aria_raw.strip()
                if isinstance(aria_raw, str) and aria_raw.strip()
                else None
            )
            width = _svg_dimension(node.get("width"))
            height = _svg_dimension(node.get("height"))
            paths = shape_count(node)
            if aria:
                role = "brandmark"
            elif paths <= 3 and width is not None and width < 30:
                role = "icon"
            else:
                role = "decorative"
            view_box = node.get("viewBox")
            entries.append({
                "role": role,
                "selector": selector,
                "viewBox": view_box if isinstance(view_box, str) else None,
                "width": width,
                "height": height,
                "outerHTML": _svg_node_to_markup(node),
                "section": section,
                "ariaLabel": aria,
                "source": "structure.json",
            })
            return
        for child in node.get("children") or []:
            visit(child, section)

    visit(structure, "none")
    return entries


def _finalize_inline_svgs(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    path = ref_dir / "inline-svgs.json"
    existing = _load_json(path)
    if existing is not None and _svg_payload_has_entries(existing):
        return
    derived = _derive_inline_svgs(structure)
    if derived:
        _write_json(path, derived)
        actions["inline-svgs.json"] = (
            f"derived {len(derived)} inline SVG(s) from structure.json"
        )
        return
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "no-inline-svgs",
        "svgs": [],
        "svgCount": 0,
        "derivedFrom": ["structure.json"],
    }
    _write_json(path, payload)
    actions["inline-svgs.json"] = "wrote no-inline-svgs sentinel"


def _css_files(ref_dir: Path) -> list[Path]:
    css_dir = ref_dir / "css"
    if not css_dir.is_dir():
        return []
    return sorted(p for p in css_dir.glob("*.css") if p.is_file())


def _read_css_text(ref_dir: Path) -> str:
    chunks: list[str] = []
    for path in _css_files(ref_dir):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _finalize_css_variables(ref_dir: Path, actions: dict[str, str]) -> None:
    css_text = _read_css_text(ref_dir)
    variables_path = ref_dir / "css" / "variables.txt"
    variables_json_path = ref_dir / "css" / "variables.json"
    if not css_text and not variables_path.exists():
        return

    stripped_css = _CSS_COMMENT_RE.sub("", css_text)
    variables = sorted(
        {name: value.strip() for name, value in _CSS_VAR_RE.findall(stripped_css)}.items()
    )
    current = ""
    if variables_path.exists():
        try:
            current = variables_path.read_text(encoding="utf-8")
        except OSError:
            current = ""
    current_has_vars = bool(_CSS_VAR_RE.search(current))

    if variables and (not current_has_vars or _is_empty_file(variables_path)):
        variables_path.parent.mkdir(parents=True, exist_ok=True)
        variables_path.write_text(
            "\n".join(f"{name}: {value}" for name, value in variables) + "\n",
            encoding="utf-8",
        )
        actions["css/variables.txt"] = "extracted CSS custom properties"
    elif not variables and _is_empty_file(variables_path):
        variables_path.parent.mkdir(parents=True, exist_ok=True)
        variables_path.write_text(
            "/* ui-clone: no CSS custom properties observed in downloaded CSS */\n",
            encoding="utf-8",
        )
        actions["css/variables.txt"] = "wrote no-css-custom-properties sentinel"

    if variables or variables_path.exists():
        payload = {
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "observation": "css-custom-properties" if variables else "no-css-custom-properties",
            "count": len(variables),
            "variables": [{"name": name, "value": value} for name, value in variables],
            "derivedFrom": [str(p.relative_to(ref_dir)) for p in _css_files(ref_dir)],
        }
        if not variables_json_path.exists() or _is_empty_file(variables_json_path):
            _write_json(variables_json_path, payload)
            actions["css/variables.json"] = "wrote CSS variables summary"


def _body_node(structure: Any) -> _JSON | None:
    for node in _walk_nodes(structure):
        if _tag(node) == "body":
            return node
    if isinstance(structure, dict):
        return structure
    return None


def _body_transition(styles: _JSON) -> str:
    transition = styles.get("transition")
    if isinstance(transition, str) and transition.strip() and transition.strip() not in _NOISE:
        return transition.strip()
    duration = str(styles.get("transition-duration") or "").strip()
    if duration and duration not in _NOISE:
        prop = str(styles.get("transition-property") or "all").strip() or "all"
        timing = str(styles.get("transition-timing-function") or "ease").strip() or "ease"
        delay = str(styles.get("transition-delay") or "0s").strip() or "0s"
        return f"{prop} {duration} {timing} {delay}"
    return "all 0s ease 0s"


def _body_class_rules(ref_dir: Path) -> list[_JSON]:
    css_text = _CSS_COMMENT_RE.sub("", _read_css_text(ref_dir))
    out: list[_JSON] = []
    seen: set[str] = set()
    for selector_group, declarations in _iter_css_rules(css_text):
        for selector in selector_group.split(","):
            selector = " ".join(selector.split())
            if not selector or not _BODY_STATE_SELECTOR_RE.search(selector):
                continue
            if not _is_valid_selector(selector):
                continue
            css_text_snippet = f"{selector} {{{' '.join(declarations.split())}}}"
            if css_text_snippet in seen:
                continue
            seen.add(css_text_snippet)
            out.append({
                "selector": selector[:160],
                "cssText": css_text_snippet[:500],
            })
    return out


def _finalize_body_state(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    path = ref_dir / "body-state.json"
    if not _is_empty_file(path):
        return
    body = _body_node(structure)
    if body is None:
        return
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "bodyTransition": _body_transition(_styles(body)),
        "bodyClassRules": _body_class_rules(ref_dir),
        "currentBodyClasses": body.get("class") or body.get("className") or "",
        "derivedFrom": ["structure.json", "css/*.css"],
        "observation": "body-state-summary",
    }
    _write_json(path, payload)
    actions["body-state.json"] = "wrote body/root state summary"


def _stable_props(styles: _JSON, props: tuple[str, ...]) -> _JSON:
    out: _JSON = {}
    for prop in props:
        value = styles.get(prop)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and value not in _NOISE:
            out[prop] = value
    return out


def _node_label(node: _JSON, index: int) -> str:
    tag = _tag(node) or "node"
    cls = _first_class(node)
    return f"{tag}.{cls}#{index}" if cls else f"{tag}#{index}"


def _build_design_bundles(structure: Any) -> _JSON:
    grouped: dict[str, dict[tuple[tuple[str, str], ...], list[str]]] = {
        kind: defaultdict(list) for kind in _BUNDLE_PROPS
    }
    nodes = _walk_nodes(structure)
    for index, node in enumerate(nodes):
        styles = _styles(node)
        if not styles:
            continue
        label = _node_label(node, index)
        for kind, props in _BUNDLE_PROPS.items():
            stable = _stable_props(styles, props)
            if not stable:
                continue
            key = tuple(sorted((k, str(v)) for k, v in stable.items()))
            grouped[kind][key].append(label)

    bundles: dict[str, list[_JSON]] = {}
    bundle_count = 0
    for kind, buckets in grouped.items():
        rows: list[_JSON] = []
        for ordinal, (key, elements) in enumerate(buckets.items(), start=1):
            # Singletons are not shared design-system evidence; keep bundles
            # focused on repeated/covarying values that generation should reuse.
            if len(elements) < 2:
                continue
            bundle_properties = {name: value for name, value in key}
            rows.append({
                "id": f"{kind}-{ordinal}",
                "properties": bundle_properties,
                "elements": elements[:80],
                "elementCount": len(elements),
            })
        bundles[kind] = rows
        bundle_count += len(rows)

    return {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "design-bundle-summary" if bundle_count else "no-shared-design-bundles",
        "bundles": bundles,
        "summary": {
            "elementCount": len(nodes),
            "bundleCount": bundle_count,
            "bundleKinds": sorted(bundles),
        },
        "derivedFrom": ["structure.json"],
    }


def _finalize_design_bundles(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    path = ref_dir / "design-bundles.json"
    if not _is_empty_file(path):
        return
    payload = _build_design_bundles(structure)
    _write_json(path, payload)
    actions["design-bundles.json"] = "wrote design bundle summary"

# --- Deterministic broad Phase-2 completion helpers -----------------------
# These helpers intentionally stay evidence-derived and site-agnostic. They
# close the gap between the documented mandatory artifact set and the fresh-run
# driver: if the browser/CSS/DOM sources already prove a value or an absence,
# write the canonical artifact instead of leaving the next agent to hand-create
# it.

# PRESENCE patterns — broad "is this SDK shipped on the page" inventory. Consumed
# by paid-features, ref-js-loader anti-cheat, canvas-replay, evidence-pack, etc.,
# so deliberately inclusive. The framer-motion row dropped the bare `motion\.`
# and unanchored `useScroll` tokens: `motion\.` matched any minified `motion.x`
# substring and `useScroll` matched custom flags like `useScrollAnimation`,
# flipping framer-motion "detected" on sites that never ship it (navercorp).
# Remaining framer signals are package-literal + Framer-specific anchored APIs.
_MOTION_LIB_PATTERNS: dict[str, str] = {
    "gsap": r"\bgsap\b|ScrollTrigger|ScrollSmoother",
    "framer-motion": (
        r"framer-motion|\bscrollYProgress\b|\buseScroll\b|\buseTransform\b"
        r"|\buseInView\b|\bwhileInView\b|\bwhileHover\b|AnimatePresence"
        # motion.<htmlTag> component factory — catches basic `<motion.div ...>`
        # usage with no hooks/while*, while a tag allowlist excludes the
        # `motion.value` / `motion.foo` substrings that caused the false positive.
        r"|motion\.(?:div|span|section|article|aside|nav|header|footer|main|ul|ol"
        r"|li|a|p|h[1-6]|img|button|form|label|svg|path|g|circle|rect|tr|td|table)\b"
    ),
    "lenis": r"\bLenis\b|lenis",
    "anime": r"anime\.js|\banime\s*\(",
    "webflow-ix2": (
        r"Webflow\s*\.\s*require\s*\(\s*['\"]ix2['\"]"
        r"|['\"]ix2['\"]\s*:\s*\{"
        r"|\bactionTypeId\b|\bdata-w-id\b|\bw-mod-ix2\b"
        r"|\bdata-wf-(?:page|site)\b|\bix2\s*\.\s*init\b"
    ),
}

# USAGE patterns — stricter "is this SDK actually CONSTRUCTING motion here"
# evidence (real call sites, not a mere import/identifier). Surfaced additively as
# external-sdks.json `usedMotion` so the generation/install decision can prefer
# libraries that drive motion over libraries merely present. Never used to NARROW
# the presence inventory above (that would break the present-but-unused consumers).
_MOTION_USAGE_PATTERNS: dict[str, str] = {
    "gsap": (
        r"gsap\s*\.\s*(?:to|from|fromTo|timeline)\s*\(|ScrollTrigger\s*\.\s*create\s*\("
        r"|\.\s*(?:to|from|fromTo)\s*\([^)]*(?:autoAlpha|xPercent|yPercent|clipPath|stagger)"
    ),
    "framer-motion": (
        r"\bscrollYProgress\b|\buseTransform\s*\(|\buseInView\s*\(|\bwhileInView\b"
        r"|\bwhileHover\b|AnimatePresence"
        r"|motion\.(?:div|span|section|article|aside|nav|header|footer|main|ul|ol"
        r"|li|a|p|h[1-6]|img|button|form|label|svg|path|g|circle|rect|tr|td|table)\b"
    ),
    "lenis": r"new\s+Lenis\s*\(",
    "anime": r"\banime\s*\(|anime\s*\.\s*timeline\s*\(",
    "webflow-ix2": r"Webflow\s*\.\s*require\s*\(\s*['\"]ix2['\"]|data-w-id",
}


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_size_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= 10
    except OSError:
        return False


def _rel_existing(ref_dir: Path, candidates: list[str]) -> list[str]:
    return [c for c in candidates if (ref_dir / c).exists()]


def _write_if_missing(ref_dir: Path, rel: str, payload: Any, actions: dict[str, str], action: str) -> None:
    path = ref_dir / rel
    if _json_size_ok(path):
        return
    _write_json(path, payload)
    actions[rel] = action


def _css_file_rels(ref_dir: Path) -> list[str]:
    return [str(p.relative_to(ref_dir)) for p in _css_files(ref_dir)]


def _section_entries(ref_dir: Path) -> list[_JSON]:
    data = _load_json(ref_dir / "section-map.json")
    if not isinstance(data, dict):
        return []
    sections = data.get("sections")
    return sections if isinstance(sections, list) else []


def _section_id(section: _JSON, index: int) -> str:
    raw = section.get("id") or section.get("sectionId")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    tag = str(section.get("tag") or "section").lower()
    cls = re.sub(r"[^a-zA-Z0-9]+", "-", str(section.get("className") or "").strip()).strip("-")
    return f"section-{index}-{cls or tag}"


def _component_name(section: _JSON, index: int) -> str:
    sid = _section_id(section, index)
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", sid) if p]
    if not parts:
        parts = ["Section", str(index)]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _first_css_source(ref_dir: Path) -> str:
    css = _css_file_rels(ref_dir)
    if css:
        return css[0]
    return "inline init"


def _hover_rule_source_chunk(
    ref_dir: Path,
    rule: _JSON,
) -> str:
    css_by_basename = {path.name: str(path.relative_to(ref_dir)) for path in _css_files(ref_dir)}
    source_file = str(rule.get("sourceFile") or "").strip()
    if source_file:
        basename = Path(source_file).name
        if basename in css_by_basename:
            return css_by_basename[basename]

    hrefs = rule.get("sourceHrefs")
    if not isinstance(hrefs, list):
        source_href = rule.get("sourceHref")
        hrefs = [source_href] if source_href else []
    for href in hrefs:
        basename = Path(unquote(urlparse(str(href)).path)).name
        if basename in css_by_basename:
            return css_by_basename[basename]
    return "inline init"


def _finalize_svg_text(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    path = ref_dir / "svg-text-elements.json"
    if _json_size_ok(path):
        return
    rows: list[_JSON] = []
    for idx, node in enumerate(_walk_nodes(structure)):
        if _tag(node) != "svg":
            continue
        text_bits: list[str] = []
        for child in node.get("children") or []:
            if isinstance(child, dict) and _tag(child) in {"text", "tspan", "title", "desc"}:
                text = child.get("text")
                if isinstance(text, str) and text.strip():
                    text_bits.append(text.strip())
        if text_bits:
            rows.append({
                "id": f"svg-text-{idx}",
                "tag": "svg",
                "text": " ".join(text_bits),
                "className": node.get("class") or node.get("className") or "",
                "source": "structure.json",
            })
    _write_json(path, rows)
    actions["svg-text-elements.json"] = "derived SVG text inventory from structure.json"


def _interesting_init_style(styles: _JSON) -> _JSON:
    out: _JSON = {}
    for key in (
        "opacity", "transform", "filter", "clip-path", "visibility",
        "transition", "transition-duration", "transition-property",
        "animation", "animation-name", "animation-duration",
    ):
        value = styles.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and value not in _NOISE and value not in {"matrix(1, 0, 0, 1, 0, 0)", "visible"}:
            out[key] = value
    return out


def _finalize_animation_init(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    rows: list[_JSON] = []
    for idx, node in enumerate(_walk_nodes(structure)):
        styles = _interesting_init_style(_styles(node))
        if not styles:
            continue
        rows.append({
            "id": f"init-{idx}",
            "selectorHint": _node_label(node, idx),
            "tag": _tag(node),
            "className": node.get("class") or node.get("className") or "",
            "initialStyles": styles,
            "source": "structure.json.styles",
        })
        if len(rows) >= 200:
            break
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "animation-init-styles" if rows else "no-nontrivial-animation-init-styles",
        "entries": rows,
        "derivedFrom": ["structure.json", "styles.json"],
    }
    _write_if_missing(ref_dir, "animation-init-styles.json", payload, actions, "derived animation init styles from computed DOM styles")
    _write_if_missing(ref_dir, "state-coupling.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "state-coupling-summary",
        "couplings": [],
        "derivedFrom": ["structure.json", "css/*.css"],
    }, actions, "wrote state coupling sentinel")


def _finalize_responsive(ref_dir: Path, structure: Any, actions: dict[str, str]) -> None:
    css_text = _read_css_text(ref_dir)
    media_values = sorted(set(re.findall(r"@media[^{}]*\((?:min|max)-width\s*:\s*([^)]+)\)", css_text)))
    _write_if_missing(ref_dir, "detected-breakpoints.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "breakpoints": media_values,
        "summary": {"count": len(media_values), "method": "css-media-query-scan"},
        "derivedFrom": _css_file_rels(ref_dir),
    }, actions, "derived breakpoints from CSS media queries")

    root = _body_node(structure) or (structure if isinstance(structure, dict) else {})
    styles = _styles(root) if isinstance(root, dict) else {}
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "single-viewport-sizing-summary",
        # Explicit marker so gates can tell this deterministic placeholder from a
        # real Step 4-C2 multi-viewport sweep. The sentinel records the settled
        # single-viewport styles only; it must not satisfy the responsive gate
        # for refs that actually respond to viewport (see responsive-detection.md
        # Step 4-C2 and responsive_sweep_remediation).
        "sentinel": True,
        "expressions": [],
        "root": {k: styles.get(k) for k in ("width", "max-width", "min-width") if styles.get(k)},
        "derivedFrom": ["structure.json", *_css_file_rels(ref_dir)],
    }
    _write_if_missing(ref_dir, "responsive/sizing-expressions.json", payload, actions, "derived sizing sentinel from computed styles")
    _write_if_missing(ref_dir, "mobile-swap.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "observation": "not-detected-by-deterministic-finalizer",
        "swaps": [],
        "derivedFrom": ["section-map.json", *_css_file_rels(ref_dir)],
    }, actions, "wrote mobile swap sentinel")


def sizing_expressions_is_unfilled_sentinel(ref_dir: Path) -> bool:
    """True when responsive/sizing-expressions.json is the deterministic
    finalizer's placeholder rather than a real Step 4-C2 sweep.

    A real sweep (responsive-detection.md Step 4-C2) writes a selector-keyed map
    of recovered expressions. The finalizer instead stamps ``_finalize_responsive``'s
    settled-only sentinel: ``sentinel: true`` / ``observation ==
    'single-viewport-sizing-summary'`` / an empty ``expressions`` list.
    """
    data = _load_json(Path(ref_dir) / "responsive" / "sizing-expressions.json")
    if not isinstance(data, dict):
        return False
    if data.get("sentinel") is True:
        return True
    if data.get("observation") == "single-viewport-sizing-summary":
        return True
    return data.get("expressions") == []


def responsive_ref_has_viewport_signals(ref_dir: Path) -> bool:
    """True when the ref responds to viewport width, so a single-viewport
    sizing sentinel is not an acceptable substitute for the Step 4-C2 sweep.

    Signals: any breakpoint in detected-breakpoints.json, or ``@media`` rules /
    ``vw`` units in the downloaded ref CSS.
    """
    ref_dir = Path(ref_dir)
    breakpoints = _load_json(ref_dir / "detected-breakpoints.json")
    if isinstance(breakpoints, dict) and breakpoints.get("breakpoints"):
        return True
    css_text = _read_css_text(ref_dir)
    if "@media" in css_text:
        return True
    return bool(re.search(r"\d\s*vw\b", css_text))


def responsive_sweep_remediation(ref_dir: Path) -> str | None:
    """Remediation message when the responsive sizing artifact is an unfilled
    finalizer sentinel while the ref actually responds to viewport; else None.

    Enforces responsive-detection.md Step 4-C2's MUST-fail contract: the
    deterministic placeholder must not, on its own, satisfy the responsive gate
    when @media rules or vw units prove the sweep still owes real expressions.
    """
    ref_dir = Path(ref_dir)
    if not sizing_expressions_is_unfilled_sentinel(ref_dir):
        return None
    if not responsive_ref_has_viewport_signals(ref_dir):
        return None
    return (
        "responsive/sizing-expressions.json is an unfilled single-viewport "
        "sentinel (no measured expressions) but the ref responds to viewport "
        "(@media rules or vw units present). Re-run the Step 4-C2 multi-viewport "
        "sweep so generation binds real sizing expressions instead of frozen px."
    )


def _hover_rules_from_css(ref_dir: Path) -> list[_JSON]:
    rules: list[_JSON] = []
    for path in _css_files(ref_dir):
        try:
            text = _CSS_COMMENT_RE.sub("", path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        for selector_group, declarations in _iter_css_rules(text):
            if ":hover" not in selector_group:
                continue
            for selector in selector_group.split(","):
                selector = " ".join(selector.split())
                if ":hover" not in selector or not _is_valid_selector(selector):
                    continue
                rules.append({
                    "selector": selector[:220],
                    "activationSelector": selector.split(":hover", 1)[0].strip()[:180],
                    "cssText": f"{selector} {{{' '.join(declarations.split())}}}"[:700],
                    "sourceFile": str(path.relative_to(ref_dir)),
                })
                if len(rules) >= 120:
                    return rules
    return rules


def _hover_activation_selector(rule: dict[str, object]) -> str:
    selector = rule.get("activationSelector") or rule.get("activation")
    if not selector:
        selector = str(rule.get("selector") or "").split(":hover", 1)[0]
    return " ".join(str(selector or "").split())


def _hover_rule_css_text(rule: dict[str, object]) -> str:
    css_text = str(rule.get("cssText") or "").strip()
    if css_text:
        return css_text[:400]
    selector = " ".join(str(rule.get("selector") or "").split())
    declarations = str(rule.get("declarations") or "").strip()
    if selector and declarations:
        return f"{selector} {{{declarations}}}"[:400]
    return ""


def _dedupe_hover_rules(rules: list[_JSON]) -> list[_JSON]:
    deduped: list[_JSON] = []
    seen: set[str] = set()
    for rule in rules:
        activation = _hover_activation_selector(rule)
        if not _is_valid_selector(activation) or activation in seen:
            continue
        seen.add(activation)
        deduped.append(rule)
    return deduped


def _live_hover_rules(ref_dir: Path) -> list[_JSON] | None:
    payload = _load_json(ref_dir / "hover-css-rules.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        return None

    provenance: list[object] = [payload.get("source")]
    derived_from = payload.get("derivedFrom")
    if isinstance(derived_from, list):
        provenance.extend(derived_from)
    if not any(
        marker in str(value or "").lower()
        for value in provenance
        for marker in ("capture-hover", "live-cssom")
    ):
        return None

    raw_rules = payload["rules"]
    if not raw_rules:
        return [] if payload.get("status") == "pass" else None

    rules = [
        rule
        for rule in raw_rules
        if isinstance(rule, dict)
        and _is_valid_selector(rule.get("selector"))
        and _is_valid_selector(_hover_activation_selector(rule))
    ]
    return _dedupe_hover_rules(rules) if rules else None


def _hover_rule_inventory(ref_dir: Path) -> tuple[list[_JSON], bool]:
    live_rules = _live_hover_rules(ref_dir)
    if live_rules is not None:
        return live_rules, True
    return _dedupe_hover_rules(_hover_rules_from_css(ref_dir)), False


def _finalize_interactions(ref_dir: Path, actions: dict[str, str]) -> None:
    hover_rules, has_live_hover = _hover_rule_inventory(ref_dir)
    interactions = [{
        "id": f"hover-{i}",
        "trigger": "hover",
        "target": _hover_activation_selector(rule),
        "timingSource": "css",
        "source": rule.get("sourceFile") or rule.get("source"),
    } for i, rule in enumerate(hover_rules[:40])]
    interaction_sources = (
        ["hover-css-rules.json"]
        if has_live_hover
        else ["hover-css-rules.json", *_css_file_rels(ref_dir)]
    )
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "hasPreloader": False,
        "interactions": interactions,
        "summary": {"hover": len(interactions), "click": 0, "scroll": 0},
        "derivedFrom": interaction_sources,
    }
    inter_current = _load_json(ref_dir / "interactions-detected.json")
    wrote_interactions = False
    if not _json_size_ok(ref_dir / "interactions-detected.json") or (isinstance(inter_current, dict) and inter_current.get("source") == "ui_clone.extraction_artifacts"):
        _write_json(ref_dir / "interactions-detected.json", payload)
        actions["interactions-detected.json"] = (
            "derived interaction summary from live hover capture"
            if has_live_hover
            else "derived interaction summary from CSS hover rules"
        )
        wrote_interactions = True

    # DAG contract: interactions-detected.json -> hover-css-rules.json. Write
    # the hover artifact after interactions so generated artifacts are not
    # immediately marked stale.
    hover_current = _load_json(ref_dir / "hover-css-rules.json")
    hover_path = ref_dir / "hover-css-rules.json"
    if not _json_size_ok(hover_path) or (isinstance(hover_current, dict) and hover_current.get("source") == "ui_clone.extraction_artifacts"):
        _write_json(hover_path, {
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "rules": hover_rules,
            "summary": {"count": len(hover_rules)},
            "derivedFrom": ["interactions-detected.json", *_css_file_rels(ref_dir)],
        })
        actions["hover-css-rules.json"] = "derived hover CSS rules from downloaded CSS"
    elif wrote_interactions and hover_path.exists():
        # Preserve richer script output but refresh mtime so the generated
        # interactions -> hover DAG edge is not immediately stale.
        os.utime(hover_path, None)
        actions["hover-css-rules.json"] = "refreshed hover CSS mtime after generated interactions"

    _write_if_missing(ref_dir, "scroll-transitions.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "transitions": [],
        "derivedFrom": ["structure.json", *_css_file_rels(ref_dir)],
    }, actions, "wrote scroll transition sentinel")
    _write_if_missing(ref_dir, "hover-deltas.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "deltas": [],
        "derivedFrom": ["hover-css-rules.json"],
    }, actions, "wrote hover delta sentinel")
    _write_if_missing(ref_dir, "hover-timing.json", {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "timings": [{"selector": r.get("selector"), "timingSource": "css"} for r in hover_rules[:40]],
        "derivedFrom": ["hover-css-rules.json"],
    }, actions, "derived hover timing summary")

def _resource_script_paths(ref_dir: Path) -> list[Path]:
    manifest = _load_json(ref_dir / "resource-manifest.json")
    if not isinstance(manifest, dict):
        return []
    raw_root = str(manifest.get("resourceRoot") or "").strip()
    root = Path(raw_root).expanduser() if raw_root else ref_dir
    if not root.is_absolute():
        root = ref_dir / root
    if not root.is_dir():
        root = ref_dir
    out: list[Path] = []
    for res in manifest.get("resources") or []:
        if not isinstance(res, dict):
            continue
        url = str(res.get("url") or "")
        path = str(res.get("path") or "")
        kind = str(res.get("kind") or "")
        ctype = str(res.get("contentType") or "")
        if kind != "script" and ".js" not in url and "javascript" not in ctype:
            continue
        if not path:
            continue
        candidates = [ref_dir / path, root / path]
        # Some manifests set resourceRoot="resources" while each entry path
        # already starts with resources/...; avoid resources/resources/...
        if raw_root and path.startswith(raw_root.rstrip("/") + "/"):
            candidates.insert(0, ref_dir / path)
        for p in candidates:
            if p.is_file():
                out.append(p)
                break
    return out


def _bundle_texts(ref_dir: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted((ref_dir / "bundles").glob("*.js"))[:40]:
        try:
            out.append((path.name, path.read_text(encoding="utf-8", errors="ignore")[:2_000_000]))
        except OSError:
            continue
    return out




def _generated_or_empty(path: Path, list_keys: tuple[str, ...] = ()) -> bool:
    if not path.exists():
        return True
    data = _load_json(path)
    if isinstance(data, list):
        return len(data) == 0
    if not isinstance(data, dict):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if isinstance(raw, list):
            return len(raw) == 0
        if not isinstance(raw, dict):
            return False
        data = raw
    if data.get("source") == "ui_clone.extraction_artifacts":
        return True
    for key in list_keys:
        value = data.get(key)
        if isinstance(value, list) and not value:
            return True
        if isinstance(value, dict) and not value:
            return True
    return False

def _finalize_bundles(ref_dir: Path, actions: dict[str, str]) -> None:
    bundles_dir = ref_dir / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in _resource_script_paths(ref_dir):
        name = src.name
        if not name.endswith(".js"):
            name = f"{name}.js"
        dest = bundles_dir / name
        if not dest.exists():
            shutil.copyfile(src, dest)
            copied += 1
    if copied:
        actions["bundles/"] = f"copied {copied} mirrored scripts into bundles"
    texts = _bundle_texts(ref_dir)
    analysis: list[_JSON] = []
    sdk_counts: dict[str, int] = {}
    used_counts: dict[str, int] = {}
    for name, text in texts:
        libs: list[str] = []
        for lib, pattern in _MOTION_LIB_PATTERNS.items():
            matches = len(re.findall(pattern, text, flags=re.IGNORECASE))
            if matches:
                libs.append(lib)
                sdk_counts[lib] = sdk_counts.get(lib, 0) + matches
            usage = _MOTION_USAGE_PATTERNS.get(lib)
            if usage:
                used = len(re.findall(usage, text, flags=re.IGNORECASE))
                if used:
                    used_counts[lib] = used_counts.get(lib, 0) + used
        transitions = sorted(set(re.findall(r"clipPath|xPercent|yPercent|autoAlpha|stagger|fromTo|scrollTrigger|\.to\(|\.from\(", text)))[:20]
        if libs or transitions:
            analysis.append({"file": name, "libraries": libs, "transitions": transitions, "size": len(text)})
    if _generated_or_empty(ref_dir / "bundle-analysis.json"):
        _write_json(ref_dir / "bundle-analysis.json", analysis)
        actions["bundle-analysis.json"] = "derived bundle analysis from mirrored scripts"
    chunks = [{"file": row["file"], "contains": row["libraries"] + row["transitions"], "key_selectors": []} for row in analysis]
    if _generated_or_empty(ref_dir / "bundle-map.json", ("chunks",)):
        _write_json(ref_dir / "bundle-map.json", {"chunks": chunks})
        actions["bundle-map.json"] = "derived bundle map from mirrored scripts"
    external_payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "detected": {k: {"matches": v} for k, v in sorted(sdk_counts.items())},
        # Additive construction-site evidence: which detected libs actually drive
        # motion (real call sites), so generation can prefer used over merely
        # present. Does NOT narrow `detected`.
        "usedMotion": {k: {"matches": v} for k, v in sorted(used_counts.items())},
        "derivedFrom": [f"bundles/{name}" for name, _ in texts],
    }
    if _generated_or_empty(ref_dir / "external-sdks.json", ("detected",)):
        _write_json(ref_dir / "external-sdks.json", external_payload)
        actions["external-sdks.json"] = "derived external SDK summary from bundle text"
    scroll_detected = {k: {"matches": v} for k, v in sdk_counts.items() if k in {"framer-motion", "lenis", "gsap"}}
    if _generated_or_empty(ref_dir / "scroll-engine.json", ("detected",)):
        _write_json(ref_dir / "scroll-engine.json", {
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "detected": scroll_detected,
            "derivedFrom": ["bundle-map.json", "external-sdks.json"],
        })
        actions["scroll-engine.json"] = "derived scroll engine summary from bundle analysis"


def _transition_reference_frames(ref_dir: Path) -> list[str]:
    frames = sorted((ref_dir / "static" / "ref").glob("*.png"))[:5]
    return [str(p.relative_to(ref_dir)) for p in frames]


def _parse_translate_funcs(tr: str) -> tuple[float | None, float | None]:
    """Extract 2D px translation from translate()/translateX()/translateY()/
    translate3d() function forms. Returns (tx, ty) in px when at least one
    translate function with a px value is present, else (None, None). Only px
    units are honored; %/other units and non-translate functions (scale, rotate,
    skew, translateZ) contribute nothing — so they cannot be read as motion."""
    def _px(v: str) -> float | None:
        m = re.fullmatch(r"\s*(-?[\d.]+)px\s*", v)
        return float(m.group(1)) if m else None

    tx = ty = 0.0
    found = False
    for fn, args in re.findall(r"(translate[XY3d]*)\(([^)]*)\)", tr):
        vals = args.split(",")
        if fn == "translateX":
            x = _px(vals[0]) if vals else None
            if x is not None:
                tx += x
                found = True
        elif fn == "translateY":
            y = _px(vals[0]) if vals else None
            if y is not None:
                ty += y
                found = True
        elif fn in ("translate", "translate3d"):
            x = _px(vals[0]) if len(vals) >= 1 else None
            y = _px(vals[1]) if len(vals) >= 2 else None
            if x is not None:
                tx += x
                found = True
            if y is not None:
                ty += y
                found = True
    return (tx, ty) if found else (None, None)


def _translate_from_transform(transform: str) -> str:
    """Captured transform -> a 2D translate from-state string, or '' when it
    carries no real 2D translation. Honest-only: scale / rotate / skew /
    translateZ-only / sub-pixel and identity transforms are NOT motion and
    return '' — a reveal from-state must be an observed positional offset."""
    tr = (transform or "").strip()
    if not tr or tr == "none":
        return ""
    tx: float | None = None
    ty: float | None = None
    m = re.match(r"matrix\(\s*([-\d.eE]+(?:\s*,\s*[-\d.eE]+){5})\s*\)", tr)
    if m:
        parts = [float(x) for x in re.split(r"\s*,\s*", m.group(1))]
        tx, ty = parts[4], parts[5]
    if tx is None:
        m3 = re.match(r"matrix3d\(\s*([-\d.eE]+(?:\s*,\s*[-\d.eE]+){15})\s*\)", tr)
        if m3:
            parts = [float(x) for x in re.split(r"\s*,\s*", m3.group(1))]
            tx, ty = parts[12], parts[13]
    if tx is None:
        tx, ty = _parse_translate_funcs(tr)
    if tx is None or ty is None:
        return ""
    if abs(tx) < 0.5 and abs(ty) < 0.5:
        return ""
    if abs(tx) < 0.5:
        return f"translateY({ty:g}px)"
    if abs(ty) < 0.5:
        return f"translateX({tx:g}px)"
    return f"translate({tx:g}px, {ty:g}px)"


def _reveal_from_channels(styles: dict[str, object]) -> dict[str, str]:
    """Observed reveal from-state channels {opacity?, transform?} from a captured
    initialStyles map. Only opacity < 1 and a non-identity translate qualify —
    absent/identity/visible values are skipped so no motion is invented."""
    out: dict[str, str] = {}
    op = styles.get("opacity")
    try:
        # Floor at 0.99 so a visually-settled element (opacity 0.999) is not
        # read as a reveal from-state.
        if isinstance(op, int | float | str) and 0.0 <= float(op) < 0.99:
            out["opacity"] = str(op)
    except (TypeError, ValueError):
        pass
    tr = _translate_from_transform(str(styles.get("transform", "") or ""))
    if tr:
        out["transform"] = tr
    return out


def _io_reveal_from_states(ref_dir: Path) -> list[dict[str, object]]:
    """Mine REAL intersection-reveal from-states from animation-init-styles.json.

    A reveal animates an element from a hidden/offset initial state to its
    settled state when it scrolls into view. The init-styles observation already
    captured those initial styles, so the floor can carry the OBSERVED from-state
    instead of an 'unresolved' stub. Honest-only: only channels actually present
    in initialStyles are emitted; returns [] when nothing usable was observed
    (caller keeps the stub). Capped to keep the floor small.
    """
    data = _load_json(ref_dir / "animation-init-styles.json") or {}
    entries = data.get("entries") if isinstance(data, dict) else None
    out: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        styles = entry.get("initialStyles")
        if not isinstance(styles, dict):
            continue
        frm = _reveal_from_channels(styles)
        if not frm:
            continue
        # Require a class selector: a bare tag (div/section/body) targets every
        # such element, which is never a per-element reveal target.
        cls = str(entry.get("className", "")).strip().split()
        if not cls:
            continue
        selector = "." + cls[0]
        if not _is_valid_selector(selector):
            continue
        key = (selector, tuple(sorted(frm.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append({"selector": selector, "from": frm})
        if len(out) >= 6:
            break
    return out


def _scroll_scrub_from_runtime(ref_dir: Path) -> list[dict[str, object]]:
    """Observed scroll-scrub entries mined from animation-runtime-dump.json.

    `scrollLinkedStyles` is the residue of inline styles that VARY across the
    scroll sweep — framer-motion useTransform, or any rAF scrub that has no
    global registry to query (see extract-animation-runtime.sh). Each row
    becomes a scroll-scrub entry whose `scrollKeyframes` carries the observed
    scroll-progress -> value curve, so generation binds the real (often
    non-linear, back-loaded) ramp instead of a flat 2-point lerp. Honest-only:
    returns [] when nothing was observed, so the caller falls back to the stub.
    """
    dump = _load_json(ref_dir / "animation-runtime-dump.json")
    if not isinstance(dump, dict):
        return []
    rows = dump.get("scrollLinkedStyles")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        selector = row.get("selector")
        varies = row.get("varies")
        by_scroll = row.get("byScroll")
        if (
            not isinstance(selector, str)
            or not isinstance(varies, list)
            or not isinstance(by_scroll, dict)
        ):
            continue
        frames: dict[str, dict[str, object]] = {
            k: v
            for k, v in by_scroll.items()
            if isinstance(k, str) and isinstance(v, dict)
        }
        fracs = sorted(frames, key=lambda s: float(s))
        if len(fracs) < 2:
            continue
        outputs: dict[str, list[object]] = {}
        settle: dict[str, float] = {}
        nonlinear = False
        for prop in varies:
            if not isinstance(prop, str):
                continue
            series = [frames[f].get(prop) for f in fracs]
            outputs[prop] = series
            final = series[-1]
            s_frac = float(fracs[-1])
            for i, f in enumerate(fracs):
                if series[i] == final and all(v == final for v in series[i:]):
                    s_frac = float(f)
                    break
            settle[prop] = s_frac
            distinct = {str(v) for v in series if v is not None}
            # Back/front-loaded curve: reaches its final value before the last
            # sampled fraction while still passing through >1 distinct value.
            # A flat lerp would only settle at the final input fraction.
            if s_frac < float(fracs[-1]) and len(distinct) > 1:
                nonlinear = True
        if not outputs:
            continue
        entry_settle = max(settle.values()) if settle else float(fracs[-1])
        out.append({
            "selector": selector,
            "scrollKeyframes": {
                "input": [float(f) for f in fracs],
                "outputs": outputs,
                "settleProgress": entry_settle,
                "easing": "measured-nonlinear" if nonlinear else "measured",
                "source": "animation-runtime-dump.json:scrollLinkedStyles",
            },
        })
    return out


def _finalize_transition_spec(ref_dir: Path, actions: dict[str, str]) -> None:
    existing = _load_json(ref_dir / "transition-spec.json")
    if (
        isinstance(existing, dict)
        and isinstance(existing.get("transitions"), list)
        and existing["transitions"]
        and existing.get("source") != "ui_clone.extraction_artifacts"
    ):
        return
    rules, has_live_hover = _hover_rule_inventory(ref_dir)
    source_chunk = _first_css_source(ref_dir)
    frames = _transition_reference_frames(ref_dir)

    def _entry(idx: int, trigger: str, target: str, animation: dict[str, object],
               chunk: str) -> dict[str, object]:
        return {
            "id": f"auto-{trigger}-{idx}",
            "trigger": trigger,
            "source_chunk": chunk,
            "bundle_branch": "settled branch observed during capture",
            "target": target,
            "selector": target,
            "animation": animation,
            "reference_frames": frames,
        }

    fallback_target = "body"
    sections = _section_entries(ref_dir)
    if sections and sections[0].get("className"):
        candidate = "." + str(sections[0].get("className", "")).strip().split()[0]
        if _is_valid_selector(candidate):
            fallback_target = candidate

    transitions: list[dict[str, object]] = []
    if isinstance(rules, list):
        for rule in rules[:12]:
            target = _hover_activation_selector(rule)
            if not _is_valid_selector(target):
                continue
            chunk = _hover_rule_source_chunk(ref_dir, rule)
            transitions.append(_entry(
                len(transitions), "hover", str(target),
                {"type": "css-hover", "cssText": _hover_rule_css_text(rule)},
                chunk,
            ))

    # One stub per detected motion signal class so the floor reflects the
    # site's motion inventory — these are DRAFT pointers for Step 5d, and
    # the whole payload stays tagged placeholder either way: a floor is
    # not a spec, and gate_spec refuses placeholder specs on motion sites.
    plan = _load_json(ref_dir / "verification-plan.json") or {}
    signals = plan.get("signals") if isinstance(plan, dict) else {}
    signals = signals if isinstance(signals, dict) else {}
    signal_stubs = (
        ("hasScrollScrub", "scroll-scrub", {"type": "scroll-scrub", "mechanism": "unresolved — mine bundle-extraction.json per Step 5d"}),
        ("hasScrollStateMachine", "scroll-state-machine", {"type": "scroll-state-machine", "mechanism": "unresolved — mine bundle-extraction.json per Step 5d"}),
        (
            "hasSwiper",
            "swiper",
            {
                "type": "swiper",
                "mechanism": (
                    "unresolved — capture the live Swiper instance and reference "
                    "frames with scripts/extract/capture-swiper-artifacts.py"
                ),
            },
        ),
    )
    for key, trigger, animation in signal_stubs:
        if not signals.get(key):
            continue
        # Prefer OBSERVED scroll-scrub curves (runtime dump) over the
        # 'unresolved' stub — same honest-over-placeholder pattern as IOReveal
        # below. The payload stays placeholder=True either way, so gate
        # behavior is unchanged; this just makes the scroll-scrub floor carry
        # the real scrollKeyframes curve for generation to bind.
        if key == "hasScrollScrub":
            observed = _scroll_scrub_from_runtime(ref_dir)
            if observed:
                for obs in observed:
                    sel = str(obs.get("selector") or fallback_target)
                    transitions.append(_entry(
                        len(transitions), trigger, sel,
                        {
                            "type": "scroll-scrub",
                            "mechanism": "observed — animation-runtime-dump.json scrollLinkedStyles",
                            "scrollKeyframes": obs.get("scrollKeyframes"),
                        },
                        source_chunk,
                    ))
                continue
        transitions.append(_entry(len(transitions), trigger, fallback_target, dict(animation), source_chunk))

    # IO-reveal: prefer OBSERVED from-states mined from animation-init-styles.json
    # over an 'unresolved' stub (Fix 4, honest-only). A reveal's captured initial
    # style IS its from-state, so emit one real entry per element that carries a
    # hidden/offset initial style; fall back to the stub only when nothing usable
    # was observed. The payload stays placeholder=True, so gate behavior is
    # unchanged — this just makes the floor actionable where evidence exists.
    if signals.get("hasIOReveal"):
        reveals = _io_reveal_from_states(ref_dir)
        if reveals:
            for rv in reveals:
                from_map = cast("dict[str, str]", rv["from"])
                channels = list(from_map.keys())
                from_state = "; ".join(f"{ch}:{val}" for ch, val in from_map.items())
                to_state = "; ".join(
                    ("opacity:1" if ch == "opacity" else "transform:none") for ch in channels
                )
                transitions.append(_entry(
                    len(transitions), "scroll-reveal", str(rv["selector"]),
                    {
                        "type": "io-reveal", "mechanism": "io-reveal",
                        "property": ",".join(channels),
                        "from": from_state, "to": to_state,
                        "measuredInitial": from_state,
                        "note": "from-state observed in animation-init-styles.json "
                                "(captured initial style; reveal trigger inferred "
                                "from the page-level hasIOReveal signal)",
                    },
                    source_chunk,
                ))
        else:
            transitions.append(_entry(
                len(transitions), "scroll-reveal", fallback_target,
                {"type": "io-reveal", "mechanism": "unresolved — mine animation-init-styles.json per Step 5d"},
                source_chunk,
            ))

    if not transitions:
        transitions.append(_entry(
            0, "page-load", fallback_target,
            {"type": "settled-load", "duration": "0s", "easing": "linear"},
            source_chunk,
        ))

    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": transitions,
        "derivedFrom": [
            "hover-css-rules.json",
            "animation-init-styles.json",
            *([] if has_live_hover else _css_file_rels(ref_dir)),
        ],
    }
    _write_json(ref_dir / "transition-spec.json", payload)
    actions["transition-spec.json"] = "derived transition spec from CSS/DOM evidence"


def _finalize_extracted(ref_dir: Path, actions: dict[str, str]) -> None:
    existing = _load_json(ref_dir / "extracted.json")
    if _json_size_ok(ref_dir / "extracted.json") and (
        not isinstance(existing, dict) or existing.get("source") != "ui_clone.extraction_artifacts"
    ):
        return
    payload = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "head": _load_json(ref_dir / "head.json") or {},
        "assets": _load_json(ref_dir / "assets.json") or {},
        "visibleImages": (_load_json(ref_dir / "visible-images.json") or {}).get("images", []),
        "fonts": _load_json(ref_dir / "fonts.json") or {},
        "sections": _section_entries(ref_dir),
        "interactions": (_load_json(ref_dir / "interactions-detected.json") or {}).get("interactions", []),
        "transitions": (_load_json(ref_dir / "transition-spec.json") or {}).get("transitions", []),
        "derivedFrom": [
            "head.json", "assets.json", "visible-images.json", "fonts.json",
            "section-map.json", "interactions-detected.json", "transition-spec.json",
        ],
    }
    _write_json(ref_dir / "extracted.json", payload)
    actions["extracted.json"] = "assembled extracted.json from canonical artifacts"


def _finalize_section_audit(ref_dir: Path, actions: dict[str, str]) -> None:
    sections = _section_entries(ref_dir)
    comps: list[_JSON] = []
    roles: list[_JSON] = []
    groups: list[_JSON] = []
    decisions: list[_JSON] = []
    for i, sec in enumerate(sections):
        sid = _section_id(sec, i)
        comp = _component_name(sec, i)
        tag = str(sec.get("tag") or "section").lower()
        entry = {
            "sectionId": sid,
            "componentName": comp,
            "sourceTag": tag,
            "sourceClass": sec.get("className") or "",
            "top": sec.get("top"),
            "height": sec.get("height"),
            "textPreview": sec.get("textPreview") or "",
        }
        comps.append(entry)
        roles.append({"sectionId": sid, "role": "landmark" if tag in {"header", "footer", "nav", "main"} else "content", "sourceTag": tag})
        groups.append({"sectionId": sid, "group": comp, "source": "section-map.json"})
        decisions.append({"sectionId": sid, "decision": "one-component-per-section", "componentName": comp})
    component_map = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "sectionCount": len(sections),
        "sections": comps,
        "components": comps,
        "derivedFrom": ["section-map.json", "dom-scaffold.json"],
    }
    generated = {
        "component-map.json": (component_map, "derived component map from section map"),
        "element-roles.json": ({"schemaVersion": 1, "source": "ui_clone.extraction_artifacts", "roles": roles, "derivedFrom": ["section-map.json"]}, "derived element roles from section map"),
        "element-groups.json": ({"schemaVersion": 1, "source": "ui_clone.extraction_artifacts", "groups": groups, "derivedFrom": ["section-map.json"]}, "derived element groups from section map"),
        "layout-decisions.json": ({"schemaVersion": 1, "source": "ui_clone.extraction_artifacts", "decisions": decisions, "derivedFrom": ["section-map.json", "dom-scaffold.json"]}, "derived layout decisions from section map"),
    }
    for rel, (payload, action) in generated.items():
        current = _load_json(ref_dir / rel)
        if not _json_size_ok(ref_dir / rel) or (isinstance(current, dict) and current.get("source") == "ui_clone.extraction_artifacts"):
            _write_json(ref_dir / rel, payload)
            actions[rel] = action


def _finalize_transition_coverage(ref_dir: Path, actions: dict[str, str]) -> None:
    spec = _load_json(ref_dir / "transition-spec.json") or {}
    transitions = spec.get("transitions") if isinstance(spec, dict) else []
    animated = []
    if isinstance(transitions, list):
        for item in transitions:
            if isinstance(item, dict):
                animated.append({
                    "id": item.get("id"),
                    "selector": item.get("selector") or item.get("target"),
                    "trigger": item.get("trigger"),
                    "decoded": {"source": item.get("source_chunk")},
                })
    cov_current = _load_json(ref_dir / "transition-coverage.json")
    if not _json_size_ok(ref_dir / "transition-coverage.json") or (isinstance(cov_current, dict) and cov_current.get("source") == "ui_clone.extraction_artifacts"):
        _write_json(ref_dir / "transition-coverage.json", {
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "animatedElements": animated,
            "derivedFrom": ["transition-spec.json", "section-map.json"],
        })
        actions["transition-coverage.json"] = "derived transition coverage from transition spec"


def _finalize_provenance(ref_dir: Path, actions: dict[str, str]) -> None:
    required: dict[str, tuple[str, list[str]]] = {
        "extracted.json": ("generated-from-artifacts", ["head.json", "section-map.json", "transition-spec.json"]),
        "transition-spec.json": ("generated-from-artifacts", ["hover-css-rules.json", "animation-init-styles.json"]),
        "animation-init-styles.json": ("computed-style", ["structure.json", "styles.json"]),
        "section-map.json": ("agent-browser-eval", ["section-map.json"]),
        "svg-text-elements.json": ("dom-snapshot", ["structure.json"]),
        "inline-svgs.json": ("dom-snapshot", ["structure.json"]),
        "responsive/sizing-expressions.json": ("computed-style", ["structure.json", "detected-breakpoints.json"]),
        "interactions-detected.json": ("generated-from-artifacts", ["hover-css-rules.json"]),
        "transition-coverage.json": ("generated-from-artifacts", ["transition-spec.json"]),
        "component-map.json": ("generated-from-artifacts", ["section-map.json", "dom-scaffold.json"]),
    }
    entries: list[_JSON] = []
    now = _utc_now()
    for artifact, (source, evidence) in required.items():
        ev = _rel_existing(ref_dir, evidence)
        if not ev:
            ev = [artifact] if (ref_dir / artifact).exists() else []
        entries.append({"path": artifact, "source": source, "evidence": ev, "generatedAt": now})
    payload = {"schemaVersion": 1, "source": "ui_clone.extraction_artifacts", "artifacts": entries}
    _write_json(ref_dir / "artifact-provenance.json", payload)
    actions["artifact-provenance.json"] = "wrote provenance for generated extraction artifacts"


def finalize_full_extraction_artifacts(ref_dir: Path) -> dict[str, str]:
    """Complete deterministic Phase-2 handoff artifacts from source evidence.

    This is broader than ``finalize_extraction_artifacts`` and is safe to call
    repeatedly: existing non-empty artifacts are preserved except provenance,
    which is regenerated to stay aligned with the current artifact set.
    """
    ref_dir = Path(ref_dir)
    actions = finalize_extraction_artifacts(ref_dir)
    structure = _load_structure(ref_dir / "structure.json")
    if structure is not None:
        _finalize_svg_text(ref_dir, structure, actions)
        _finalize_animation_init(ref_dir, structure, actions)
        _finalize_responsive(ref_dir, structure, actions)
    _finalize_interactions(ref_dir, actions)
    _finalize_bundles(ref_dir, actions)
    _finalize_transition_spec(ref_dir, actions)
    _finalize_section_audit(ref_dir, actions)
    _finalize_transition_coverage(ref_dir, actions)
    # Assemble last so extracted.json is fresher than generated child artifacts
    # such as component-map.json and transition-coverage.json.
    _finalize_extracted(ref_dir, actions)
    _finalize_provenance(ref_dir, actions)
    return actions


def refresh_extracted_artifact(ref_dir: Path) -> dict[str, str]:
    """Refresh only the assembled ``extracted.json`` handoff.

    Late state-capture steps (hover/scroll/runtime evidence) can update
    canonical source artifacts after Phase 2 has assembled ``extracted.json``.
    Pre-generate should refresh that deterministic handoff without silently
    minting missing provenance or other audit artifacts; the provenance gate
    must still fail when the evidence trail is absent.

    A current handoff is intentionally left untouched. Rewriting identical
    JSON here advances its mtime past an already-enriched generation plan, so
    the pre-generate gate would make that plan stale merely by checking it.
    """
    from ui_clone.dag import check_staleness

    ref_dir = Path(ref_dir)
    extracted = ref_dir / "extracted.json"
    if extracted.is_file() and not any(
        issue.stale == "extracted.json" for issue in check_staleness(ref_dir)
    ):
        return {}

    actions: dict[str, str] = {}
    _finalize_extracted(ref_dir, actions)
    return actions
