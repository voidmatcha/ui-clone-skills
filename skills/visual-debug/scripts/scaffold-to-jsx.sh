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


# Tags whose elements are void in HTML (self-closing in JSX).
VOID_TAGS = {
    "area","base","br","col","embed","hr","img","input","link",
    "meta","param","source","track","wbr",
}
# Tags that don't render content — skip in JSX entirely.
SKIP_TAGS = {"script","style","link","meta","noscript","template"}
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
    locally-rewritten equivalent. Codex audit gap #3 — fixes the
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
        ck = kebab_to_camel(k)
        # Escape backticks/double-quotes inside values.
        v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'{ck}: "{v_safe}"')
    return "{{ " + ", ".join(items) + " }}"


def rewrite_asset_url(v):
    """Rewrite ref CDN/image-optimizer URLs to local public asset paths."""
    if not isinstance(v, str) or not v:
        return v
    base = os.path.basename(v.split("?", 1)[0])
    m = re.search(r'/cdn-cgi/image/[^/]+/(.+)', v)
    if m:
        base = os.path.basename(m.group(1))
    ext = os.path.splitext(base)[1].lower()
    if ext in (".mp4", ".webm", ".mov"):
        return f"/videos/{base}"
    if ext in (".webp", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".avif"):
        return f"/images/{base}"
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


def safe_class_name(cls):
    """JSX className string — strip newlines, double-quotes."""
    return (cls or "").replace("\n", " ").replace('"', "'").strip()[:120]


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
    text = node.get("text", "") or ""
    cls = safe_class_name(node.get("class", ""))
    styles = node.get("styles") or {}
    children = node.get("children") or []

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
        "src": "src", "href": "href", "alt": "alt", "poster": "poster",
        "srcset": "srcSet", "sizes": "sizes", "type": "type",
        "target": "target", "rel": "rel",
        "aria-label": "aria-label", "title": "title", "role": "role",
        "data-src": "data-src", "data-poster": "data-poster",
    }
    attr_emit: dict[str, str] = {}
    for src_key, jsx_key in attr_map.items():
        v = node.get(src_key)
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
        v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
        attr_emit[jsx_key] = v_safe

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
            v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
            attr_emit[jsx_key] = v_safe

    extra_attrs = "".join(
        f' {k}="{v}"' for k, v in attr_emit.items()
    )
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
        rendered = [render(c, indent + 1, hover_rules) for c in children]
        rendered_chunks = [r for r in rendered if r]
        if pseudo_jsx:
            # ::before precedes the real children, ::after follows them.
            if before_ps:
                rendered_chunks.insert(0, _render_pseudo("before", before_ps, indent + 1))
            if after_ps:
                rendered_chunks.append(_render_pseudo("after", after_ps, indent + 1))
        child_str = "\n" + "\n".join(rendered_chunks) + "\n" + pad

    # Text content (verbatim, escaped).
    if text and not children and not pseudo_jsx:
        return f'{pad}<{tag}{cls_attr}{style_attr}>{escape_jsx_text(text)}</{tag}>'
    if text and (children or pseudo_jsx):
        # Mixed: text + children. Place text at top, then children/pseudos.
        return f'{pad}<{tag}{cls_attr}{style_attr}>\n{"  " * (indent + 1)}{escape_jsx_text(text)}{child_str}</{tag}>'
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
    target_cls = cls.split()[0] if cls else ""

    def walk(node):
        if not isinstance(node, dict) or id(node) in consumed:
            return None
        if (node.get("tag", "").lower() == target_tag
                and (
                    (sid and node.get("id") == sid)
                    or (target_cls and target_cls in (node.get("class", "") or ""))
                )):
            return node
        for c in node.get("children", []) or []:
            m = walk(c)
            if m:
                return m
        return None

    found = walk(root)
    if found is not None:
        consumed.add(id(found))
    return found


structure = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out_dir = Path(sys.argv[3])

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


written = []
exports = []
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
    hover_rules = []  # Fix 19 — collected during render(); emitted as <style>.
    dominant_bg = sec.get("dominantBg") if isinstance(sec, dict) else None
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
    if subtree is None:
        # Couldn't locate the subtree — emit a stub that imports the section
        # placeholder. Phase-5b visual-judge will surface this gap.
        body = f'  <section data-scaffold-warn="subtree-not-found-for-{name}" />'
    else:
        body = render(subtree, indent=2, hover_rules=hover_rules)
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

# Also emit a barrel index so page.tsx can `import * as Sections from "./components"`.
index_body = "\n".join(f'export {{ default as {n} }} from "./{n}";' for n in exports) + "\n"
(out_dir / "index.ts").write_text(index_body, encoding="utf-8")

# Fix 15 + Codex universality audit CRITICAL: prior version always
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
root_styles = structure.get("styles") or {}
root_cls_attr = f' className="{root_cls}"' if root_cls else ""
root_style_attr = f" style={style_to_jsx(root_styles)}" if root_styles else ""

# Sections in section-map order — they're already ordered by `top` upstream.
section_jsx = "\n".join(f"      <{n} />" for n in exports)


def _emit_next_page() -> Path:
    page_dir = out_dir.parent / "app"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / "page.tsx"
    imports = "\n".join(
        f'import {n} from "@/components/{n}";' for n in exports
    )
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
        f"{section_jsx}\n"
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
        f"{section_jsx}\n"
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
PY
