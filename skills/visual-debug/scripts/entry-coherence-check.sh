#!/usr/bin/env bash
# entry-coherence-check.sh — fail when impl has a mixed/inconsistent
# entry-point shape that lets the agent cheat by leaving raw markup in
# index.html while pretending to ship a React tree.
#
#
# Usage:
#   entry-coherence-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/entry-coherence.json
#   { schemaVersion: 1, status: pass|fail|skip, stack, entry, violations }
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: entry-coherence-check.sh <ref-dir> [<impl-root>]" >&2
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

OUT_PATH="$REF_DIR/entry-coherence.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "entry-coherence: skip (no impl)"
  exit 0
fi

python3 - "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

impl_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])

violations: list[dict] = []


def read_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# Stack inference from package.json.
pkg_json_path = impl_root / "package.json"
pkg_deps: dict[str, str] = {}
if pkg_json_path.is_file():
    try:
        pkg_data = json.loads(read_safe(pkg_json_path))
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            d = pkg_data.get(key, {})
            if isinstance(d, dict):
                pkg_deps.update({k: str(v) for k, v in d.items()})
    except (OSError, ValueError):
        pass

has_next = "next" in pkg_deps
has_vite = "vite" in pkg_deps or "@vitejs/plugin-react" in pkg_deps
has_remix = "@remix-run/react" in pkg_deps
has_react = "react" in pkg_deps
has_vue = "vue" in pkg_deps or "@vue/runtime-dom" in pkg_deps
has_svelte = "svelte" in pkg_deps
has_solid = "solid-js" in pkg_deps
has_astro = "astro" in pkg_deps
has_sveltekit = "@sveltejs/kit" in pkg_deps

#
non_react_stack = None
remix_react = has_remix
if remix_react:
    non_react_stack = None  # Remix IS React; just don't enforce vite-main
elif has_astro:
    non_react_stack = "astro"
elif has_sveltekit or has_svelte:
    non_react_stack = "svelte"
elif has_vue and not has_react:
    non_react_stack = "vue"
elif has_solid and not has_react:
    non_react_stack = "solid"


residue_violations: list[dict] = []
NEXT_RESIDUE_PATHS = [
    impl_root / "public" / "_next",
    impl_root / "_next",
    impl_root / ".next",
]
VITE_RESIDUE_PATHS = [
    impl_root / "public" / "assets" / "vite-manifest.json",
    impl_root / "public" / ".vite",
]
# Apply Next-residue detection to ANY non-Next stack (vite, astro,
# svelte, vue, solid, remix-on-vite — none should ship .next/).
if not has_next:
    for rp in NEXT_RESIDUE_PATHS:
        if rp.exists():
            residue_violations.append({
                "kind": "cross-framework-residue",
                "detail": (
                    f"Impl contains Next.js build residue at "
                    f"`{rp.relative_to(impl_root)}` — leftover from a "
                    "previous Next scaffold. Remove or regenerate."
                ),
                "path": str(rp.relative_to(impl_root)),
                "frameworks": "non-next-with-next-residue",
            })
if has_next and not (has_vite or has_remix):
    for rp in VITE_RESIDUE_PATHS:
        if rp.exists():
            residue_violations.append({
                "kind": "cross-framework-residue",
                "detail": (
                    f"Next impl contains Vite manifest residue at "
                    f"`{rp.relative_to(impl_root)}`."
                ),
                "path": str(rp.relative_to(impl_root)),
                "frameworks": "next-with-vite-residue",
            })


if non_react_stack:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail" if residue_violations else "skip",
        "reason": (
            f"non-React stack ({non_react_stack}); entry-coherence "
            "rules are React-specific (but cross-framework residue "
            "still checked)"
        ),
        "implRoot": str(impl_root),
        "stack": non_react_stack,
        "violations": residue_violations,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"entry-coherence: skip (stack={non_react_stack}, residue={len(residue_violations)})")
    sys.exit(1 if residue_violations else 0)


# Carry over residue violations into the main React path.
violations.extend(residue_violations)


# (Residue detection moved earlier — fires before stack-specific
# skip so non-React stacks also get cross-framework checks.)

def _scripts() -> str:
    if not pkg_json_path.is_file():
        return ""
    try:
        pj = json.loads(read_safe(pkg_json_path))
        scripts = pj.get("scripts") or {}
        if isinstance(scripts, dict):
            return " ".join(str(v) for v in scripts.values()).lower()
    except (OSError, ValueError):
        pass
    return ""


script_text = _scripts()
has_vite_config = any(
    (impl_root / cf).is_file()
    for cf in ("vite.config.ts", "vite.config.js", "vite.config.mjs")
)
has_next_config = any(
    (impl_root / cf).is_file()
    for cf in ("next.config.ts", "next.config.js", "next.config.mjs")
)
has_webpack_config = any(
    (impl_root / cf).is_file()
    for cf in ("webpack.config.js", "webpack.config.ts")
)
has_vite_scripts = "vite" in script_text
has_next_scripts = ("next dev" in script_text) or ("next build" in script_text)
has_webpack_scripts = "webpack" in script_text

# Effective signal: package dep AND (config file OR build script).
vite_effective = has_vite and (has_vite_config or has_vite_scripts)
next_effective = has_next and (has_next_config or has_next_scripts)

stack = "unknown"
if next_effective and vite_effective:
    violations.append({
        "kind": "mixed-stack",
        "detail": "both Next and Vite have effective evidence (config or build script)",
    })
    stack = "mixed:next+vite"
elif next_effective:
    stack = "next"
elif vite_effective:
    stack = "vite"
elif has_remix:
    stack = "remix"
elif has_react:
    stack = "react-other"

# Declared-but-not-built: agent put `vite` in deps but the actual build
# is webpack. Flag this as a mixed-build smell.
if has_vite and not vite_effective and (has_webpack_config or has_webpack_scripts):
    violations.append({
        "kind": "declared-stack-not-effective",
        "detail": (
            "package.json declares `vite` but there's no vite.config.* "
            "and no vite-using build script; effective bundler appears "
            "to be webpack — clone scaffold mismatch"
        ),
    })


# Entry-point detection.
src_main_candidates = [
    impl_root / "src" / "main.tsx",
    impl_root / "src" / "main.jsx",
    impl_root / "src" / "main.ts",
    impl_root / "src" / "main.js",
]
src_main = next((p for p in src_main_candidates if p.is_file()), None)

next_app_page = None
for cand in (
    impl_root / "app" / "page.tsx",
    impl_root / "app" / "page.jsx",
    impl_root / "app" / "page.ts",
    impl_root / "app" / "page.js",
    impl_root / "src" / "app" / "page.tsx",
    impl_root / "src" / "app" / "page.jsx",
):
    if cand.is_file():
        next_app_page = cand
        break

next_pages_index = None
for cand in (
    impl_root / "pages" / "index.tsx",
    impl_root / "pages" / "index.jsx",
    impl_root / "pages" / "index.ts",
    impl_root / "pages" / "index.js",
    impl_root / "src" / "pages" / "index.tsx",
    impl_root / "src" / "pages" / "index.jsx",
):
    if cand.is_file():
        next_pages_index = cand
        break

index_html = impl_root / "index.html"


# A3 — coexisting entry points (one of the strongest cheat signals).
if src_main is not None and (next_app_page is not None or next_pages_index is not None):
    coexisting = [str(src_main.relative_to(impl_root))]
    if next_app_page:
        coexisting.append(str(next_app_page.relative_to(impl_root)))
    if next_pages_index:
        coexisting.append(str(next_pages_index.relative_to(impl_root)))
    violations.append({
        "kind": "coexisting-entry-points",
        "detail": (
            "src/main.* and app/page.* (or pages/index.*) both present — "
            "impl must have ONE coherent entry path"
        ),
        "files": coexisting,
    })


# Stack-specific canonical-entry requirement.
if stack == "vite":
    if src_main is None:
        violations.append({
            "kind": "missing-vite-entry",
            "detail": "Vite+React stack requires src/main.{jsx,tsx} as the rendered entry",
        })
elif stack == "next":
    if next_app_page is None and next_pages_index is None:
        violations.append({
            "kind": "missing-next-entry",
            "detail": (
                "Next stack requires app/page.{jsx,tsx} (App Router) or "
                "pages/index.{jsx,tsx} (Pages Router)"
            ),
        })


# index.html shape — Vite expects an empty mount point. Anything that
# looks like raw site markup pasted in is the cheat.
HTML_CONTENT_TAGS_RE = re.compile(
    r"<\s*(section|article|nav|main|header|footer|h[1-6]|"
    r"p|ul|ol|table|form|video|iframe|figure)[\s>]",
    re.IGNORECASE,
)
if index_html.is_file():
    html_text = read_safe(index_html)
    body_match = re.search(
        r"<body[^>]*>(.*?)</body\s*>", html_text, flags=re.IGNORECASE | re.DOTALL,
    )
    body_inner = body_match.group(1) if body_match else html_text
    content_tag_count = len(HTML_CONTENT_TAGS_RE.findall(body_inner))
    body_text_chars = len(re.sub(r"<[^>]+>", "", body_inner).strip())
    # Heuristic: Vite scaffold body is `<div id="root"></div><script
    # type="module" src="/src/main.{tsx,jsx}"></script>` — under 200
    # text chars, zero content tags. Trip the gate on EITHER:
    #   (a) ≥5 content tags from the layout-bearing set (strong signal
    #       of pasted markup regardless of text volume), OR
    #   (b) ≥3 content tags AND ≥800 chars of body text (smaller paste
    #       but still semantic content the React app should own).
    # The (a) branch catches dense tag dumps where the agent stripped
    # most text; the (b) branch catches verbose paragraphs with thin
    # tag wrapping. Either one means the agent put renderable content
    # in the mount file instead of in a component.
    if content_tag_count >= 5 or (content_tag_count >= 3 and body_text_chars >= 800):
        violations.append({
            "kind": "raw-markup-in-index-html",
            "detail": (
                f"index.html body has {content_tag_count} content tags and "
                f"{body_text_chars} text chars — looks like pasted ref markup, "
                "not a React mount point"
            ),
            "file": "index.html",
        })


status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "stack": stack,
    "entryFiles": {
        "viteMain": str(src_main.relative_to(impl_root)) if src_main else None,
        "nextAppPage": (
            str(next_app_page.relative_to(impl_root)) if next_app_page else None
        ),
        "nextPagesIndex": (
            str(next_pages_index.relative_to(impl_root)) if next_pages_index else None
        ),
        "indexHtml": "index.html" if index_html.is_file() else None,
    },
    "violations": violations,
    "rule": (
        "Impl must have ONE coherent entry path matching the declared stack. "
        "Vite+React → src/main.{jsx,tsx}. Next App → app/page.tsx. Coexisting "
        "entry points, mixed Vite+Next deps, or raw ref markup in index.html "
        "all indicate the agent is gaming gates via scaffold residue."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"entry-coherence: stack={stack} → {status} "
    f"({len(violations)} violation(s)) → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
