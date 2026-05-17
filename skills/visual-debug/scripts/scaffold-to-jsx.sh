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


def kebab_to_camel(s):
    parts = s.split("-")
    return parts[0] + "".join(p.title() for p in parts[1:])


def style_to_jsx(styles):
    """Convert a dict of CSS prop → string into a JSX-style object literal."""
    if not styles:
        return ""
    items = []
    for k, v in styles.items():
        ck = kebab_to_camel(k)
        # Escape backticks/double-quotes inside values.
        v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
        items.append(f'{ck}: "{v_safe}"')
    return "{{ " + ", ".join(items) + " }}"


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


def render(node, indent=2):
    """Render a node (and its subtree) to JSX. Returns the string."""
    if not isinstance(node, dict):
        return ""
    tag = (node.get("tag") or "div").lower()
    if tag in SKIP_TAGS:
        return ""
    text = node.get("text", "") or ""
    cls = safe_class_name(node.get("class", ""))
    styles = node.get("styles") or {}
    children = node.get("children") or []

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
    extra_attrs = ""
    for src_key, jsx_key in attr_map.items():
        v = node.get(src_key)
        if not isinstance(v, str) or not v:
            continue
        # For <img src> rewrite the CDN URL to the locally-downloaded path
        # under /images/ or /videos/ so Next.js serves from impl/public.
        if src_key in ("src", "poster"):
            import os as _os, re as _re
            base = _os.path.basename(v.split("?")[0])
            m = _re.search(r'/cdn-cgi/image/[^/]+/(.+)', v)
            if m:
                base = _os.path.basename(m.group(1))
            ext = _os.path.splitext(base)[1].lower()
            if ext in (".mp4", ".webm", ".mov"):
                v = f"/videos/{base}"
            elif ext in (".webp", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".avif"):
                v = f"/images/{base}"
            # else leave URL as-is (rare)
        v_safe = v.replace("\\", "\\\\").replace('"', '\\"')
        extra_attrs += f' {jsx_key}="{v_safe}"'
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
        rendered = [render(c, indent + 1) for c in children]
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
    # Strip CSS Module hash suffixes like dga_hero__AjMaf → DgaHero.
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
    (CSS-Module suffixes like dga_section__k3uwv-2, -3, ...), 8 of 15
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
    if subtree is None:
        # Couldn't locate the subtree — emit a stub that imports the section
        # placeholder. Phase-5b visual-judge will surface this gap.
        body = f'  <section data-scaffold-warn="subtree-not-found-for-{name}" />'
    else:
        body = render(subtree, indent=2)
    file_body = (
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh (Fix 13).\n"
        "// DO NOT hand-edit at the JSX level — re-run the transpiler if the ref changes.\n"
        "// Event handlers / state / animation triggers can be wired in a wrapper component.\n"
        "\n"
        f"export default function {name}() {{\n"
        f"  return (\n"
        f"{body}\n"
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

# Fix 15 — auto-emit page.tsx. V11 (220c969) showed that the transpiler
# produced pixel-accurate per-section components, but agent-written page.tsx
# wrapped them in a misconfigured outer element so section-compare couldn't
# match the impl to the right ref sections (hero/lineInTheSand/stats stayed
# at ~900k AE because the impl <main> wrapper was 19826px tall — the entire
# page — matched against ref's 700px hero section).
#
# Auto-generated page.tsx removes that wiring drift by mirroring the
# structure.json root tag/styles and importing sections in section-map order.
page_dir = out_dir.parent / "app"
page_dir.mkdir(parents=True, exist_ok=True)
page_path = page_dir / "page.tsx"

# Root element from structure.json (typically <main> or <body>).
root_tag = (structure.get("tag") or "main").lower()
root_cls = safe_class_name(structure.get("class") or "")
root_styles = structure.get("styles") or {}
root_cls_attr = f' className="{root_cls}"' if root_cls else ""
root_style_attr = f" style={style_to_jsx(root_styles)}" if root_styles else ""

# Sections in section-map order — they're already ordered by `top` upstream.
imports = "\n".join(f'import {n} from "@/components/{n}";' for n in exports)
section_jsx = "\n".join(f"      <{n} />" for n in exports)

page_body = (
    "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh (Fix 15).\n"
    "// DO NOT hand-edit — re-run the transpiler if the ref changes.\n"
    "// Section composition + root wrapper mirror the ref DOM structure exactly.\n"
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
page_path.write_text(page_body, encoding="utf-8")

print(f"scaffold-to-jsx: wrote {len(written)} components to {out_dir}")
for name in written[:6]:
    print(f"  - {name}")
if len(written) > 6:
    print(f"  ... +{len(written) - 6} more")
print(f"scaffold-to-jsx: wrote page.tsx at {page_path}")
print(f"  root tag: <{root_tag}>, imports: {len(exports)} sections")
PY
