#!/usr/bin/env bash
# html-paste-check.sh — fail when impl's entry HTML mirrors the ref's
# captured DOM structure or directly loads ref bundles / inlines ref CSS.
#
#
#   1. STRUCTURAL similarity: impl's index/page HTML tag-multiset
#      mirrors ref's dom-scaffold.json tag-multiset (>=70% similarity).
#      Catches when the agent pastes the whole ref body into Next's
#      app/page.tsx return value or into Vite index.html.
#
#   2. SCRIPT THEFT: <script src="..."> in impl entry HTML pointing at
#      anything resembling the ref's bundle filenames captured in
#      bundle-map.json (e.g. /js/navercorp.min.js, /assets/main.*.js).
#      Hot-loading the ref's bundle bypasses every other gate.
#
#   3. INLINE CSS theft: <style> blocks in impl entry HTML byte-similar
#      to any ref CSS bundle captured under <ref>/bundles/*.css.
#      Bulk-pasting compiled ref CSS short-circuits styling work.
#
# Inputs the check uses:
#   <ref-dir>/dom-scaffold.json     — for structural multiset compare
#   <ref-dir>/bundle-map.json       — for script-name watchlist
#   <ref-dir>/bundles/*.css         — for inline-CSS similarity
#
# Outputs: <ref-dir>/html-paste.json
#   { status, implRoot, similarity, violations: [{kind, file, ...}] }
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: html-paste-check.sh <ref-dir> [<impl-root>]" >&2
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

OUT_PATH="$REF_DIR/html-paste.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "html-paste: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

violations: list[dict] = []


def read_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# Pick candidate entry files across all common frameworks.
# Universality audit HIGH FN: prior version only checked Next
# App Router + Pages Router + raw index.html. Remix, Astro, Svelte,
# Vue, Solid, plain webpack/parcel React all have their own entries
# that can carry pasted ref markup.
candidates: list[Path] = []
# Raw HTML entries.
for cand in (
    impl_root / "index.html",
    impl_root / "src" / "index.html",
    impl_root / "public" / "index.html",
):
    if cand.is_file():
        candidates.append(cand)

# JSX/TSX entries — Next App Router, Pages Router, Remix routes,
# webpack/parcel/CRA-style src/App, src/index.
for cand in (
    impl_root / "app" / "page.tsx",
    impl_root / "app" / "page.jsx",
    impl_root / "app" / "layout.tsx",
    impl_root / "app" / "root.tsx",  # Remix root
    impl_root / "app" / "root.jsx",
    impl_root / "pages" / "index.tsx",
    impl_root / "pages" / "index.jsx",
    impl_root / "src" / "app" / "page.tsx",
    impl_root / "src" / "App.tsx",
    impl_root / "src" / "App.jsx",
    impl_root / "src" / "index.tsx",
    impl_root / "src" / "index.jsx",
):
    if cand.is_file():
        candidates.append(cand)

# Remix route files — anything under app/routes/.
remix_routes = impl_root / "app" / "routes"
if remix_routes.is_dir():
    for p in remix_routes.rglob("*"):
        if p.is_file() and p.suffix in {".tsx", ".jsx"}:
            candidates.append(p)

# Astro pages — Astro uses .astro components, with src/pages/index.astro
# as a typical entry.
astro_pages = impl_root / "src" / "pages"
if astro_pages.is_dir():
    for p in astro_pages.rglob("*"):
        if p.is_file() and p.suffix in {".astro", ".tsx", ".jsx"}:
            candidates.append(p)

# Vue/Svelte/Solid root templates.
for cand in (
    impl_root / "src" / "App.vue",
    impl_root / "src" / "App.svelte",
    impl_root / "src" / "App.solid.tsx",
    impl_root / "src" / "routes" / "+layout.svelte",  # SvelteKit
    impl_root / "src" / "routes" / "+page.svelte",
):
    if cand.is_file():
        candidates.append(cand)


# 1. Structural similarity vs dom-scaffold.json.
scaffold_path = ref_dir / "dom-scaffold.json"
ref_tag_seq: list[str] = []
if scaffold_path.is_file():
    try:
        scaffold = json.loads(read_safe(scaffold_path))

        def walk(node: dict, depth: int = 0, max_depth: int = 12) -> None:
            if depth > max_depth or not isinstance(node, dict):
                return
            tag = node.get("tag", "")
            if tag:
                ref_tag_seq.append(tag.lower())
            for c in node.get("children", []) or []:
                walk(c, depth + 1, max_depth)

        walk(scaffold.get("tree", {}))
    except (OSError, ValueError):
        ref_tag_seq = []


HTML_TAG_RE = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9]*)\b")
HTML_TAGS = {
    "html", "body", "main", "header", "footer", "nav", "aside", "section",
    "article", "div", "span", "a", "button", "img", "video", "audio",
    "picture", "source", "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul",
    "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tr", "th",
    "td", "form", "input", "textarea", "select", "option", "label",
    "iframe", "canvas", "svg", "path", "g", "circle", "rect", "figure",
    "figcaption", "blockquote", "pre", "code", "details", "summary",
    "dialog",
}


def extract_tag_seq(text: str) -> list[str]:
    seq: list[str] = []
    body_match = re.search(
        r"<body[^>]*>(.*?)</body\s*>", text, flags=re.IGNORECASE | re.DOTALL,
    )
    scope = body_match.group(1) if body_match else text
    # Strip JSX expressions { ... } and JS strings to avoid noise.
    scope = re.sub(r"\{[^{}]*\}", "", scope)
    for m in HTML_TAG_RE.finditer(scope):
        tag = m.group(1).lower()
        if tag in HTML_TAGS:
            seq.append(tag)
    return seq


# Reference bundle name watchlist for script-theft detection.
bundle_map_path = ref_dir / "bundle-map.json"
bundle_filenames: set[str] = set()
ref_script_hosts: set[str] = set()


def _ingest_url(u: str) -> None:
    if not u or not isinstance(u, str):
        return
    name = u.rstrip("/").split("/")[-1].split("?")[0]
    if name:
        bundle_filenames.add(name)
    if u.startswith(("http://", "https://")):
        mh = re.match(r"https?://([^/\:\?\#]+)", u)
        if mh:
            ref_script_hosts.add(mh.group(1).lower())


if bundle_map_path.is_file():
    try:
        bm = json.loads(read_safe(bundle_map_path))
        for url in bm.get("scriptUrls", []) or []:
            _ingest_url(url)
        for url in bm.get("bundleUrls", []) or []:
            _ingest_url(url)
        for entry in bm.get("scripts", []) or []:
            if isinstance(entry, dict):
                _ingest_url(entry.get("src", ""))
    except (OSError, ValueError):
        pass


# Reference CSS bundle bodies for inline-style similarity.
ref_css_bodies: list[str] = []
bundles_dir = ref_dir / "bundles"
if bundles_dir.is_dir():
    for css_path in bundles_dir.glob("*.css"):
        body = read_safe(css_path)
        if len(body) > 500:  # ignore tiny shims
            ref_css_bodies.append(body)


SCRIPT_SRC_RE = re.compile(
    r"<\s*script[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE,
)
STYLE_BLOCK_RE = re.compile(
    r"<\s*style[^>]*>(.*?)</\s*style\s*>", re.IGNORECASE | re.DOTALL,
)


for path in candidates:
    rel = path.relative_to(impl_root)
    text = read_safe(path)
    if not text:
        continue

    # 1. Structural similarity vs dom-scaffold.
    #
    if ref_tag_seq:
        impl_seq = extract_tag_seq(text)
        is_component_file = rel.suffix in {
            ".tsx", ".jsx", ".astro", ".vue", ".svelte",
        }
        threshold = 0.90 if is_component_file else 0.70
        if len(impl_seq) >= 20:  # too small to compare meaningfully
            ref_c = Counter(ref_tag_seq)
            impl_c = Counter(impl_seq)
            all_tags = set(ref_c) | set(impl_c)
            overlap = sum(min(ref_c.get(t, 0), impl_c.get(t, 0)) for t in all_tags)
            union = sum(max(ref_c.get(t, 0), impl_c.get(t, 0)) for t in all_tags)
            sim = (overlap / union) if union else 0.0
            if sim >= threshold:
                # JSX-file corroboration: require dangerouslySetInnerHTML
                # or a big text blob to avoid flagging hand-authored
                # React trees that happen to mirror ref structure.
                corroborated = True
                if is_component_file:
                    has_dangerous = "dangerouslySetInnerHTML" in text
                    body_text_chars = len(re.sub(r"<[^>]+>", "", text))
                    corroborated = has_dangerous or body_text_chars >= 1500
                if corroborated:
                    violations.append({
                        "kind": "structural-similarity-to-scaffold",
                        "file": str(rel),
                        "similarity": round(sim, 3),
                        "threshold": threshold,
                        "implTagCount": len(impl_seq),
                        "refTagCount": len(ref_tag_seq),
                        "detail": (
                            f"impl entry tag-multiset is {round(sim * 100)}% "
                            "similar to ref's dom-scaffold — likely a paste "
                            "of the ref body, not a React mount"
                        ),
                    })

    # 2. Script theft.
    # Universality audit HIGH FP: bare basename matches like
    # `main.js` / `app.js` / `vendor.js` collide across virtually
    # every site, so a legit clone with its own main.js would
    # false-trigger this. Require non-generic basename OR a host
    # match against bundle-map's known hosts.
    GENERIC_SCRIPT_NAMES = {
        "main.js", "app.js", "index.js", "bundle.js", "vendor.js",
        "common.js", "chunk.js", "runtime.js", "polyfills.js",
        "main.min.js", "app.min.js", "bundle.min.js",
    }
    for m in SCRIPT_SRC_RE.finditer(text):
        src = m.group(1)
        name = src.rstrip("/").split("/")[-1].split("?")[0]
        if name in bundle_filenames and name not in GENERIC_SCRIPT_NAMES:
            violations.append({
                "kind": "ref-bundle-script-tag",
                "file": str(rel),
                "src": src,
                "detail": (
                    f"impl entry HTML loads `{name}` which matches a "
                    "reference bundle name — impl is hot-loading the ref's JS"
                ),
            })
        elif name in bundle_filenames and name in GENERIC_SCRIPT_NAMES:
            # Generic name collision — only flag when paired with host
            # evidence (the same generic name being loaded from one of
            # the ref's actual hosts is the cheat signal).
            if src.startswith(("http://", "https://")):
                mh = re.match(r"https?://([^/\:\?\#]+)", src)
                src_host = mh.group(1).lower() if mh else ""
                if src_host and src_host in ref_script_hosts:
                    violations.append({
                        "kind": "ref-bundle-script-tag-host-corroborated",
                        "file": str(rel),
                        "src": src,
                        "detail": (
                            "generic script name but loaded from a host "
                            "present in bundle-map.json"
                        ),
                    })

    # 3. Inline CSS similarity.
    if ref_css_bodies:
        for m in STYLE_BLOCK_RE.finditer(text):
            block = m.group(1).strip()
            if len(block) < 500:
                continue
            for ref_body in ref_css_bodies:
                ratio = SequenceMatcher(
                    None, block[:4000], ref_body[:4000],
                ).quick_ratio()
                if ratio >= 0.70:
                    violations.append({
                        "kind": "inline-css-matches-ref-bundle",
                        "file": str(rel),
                        "similarity": round(ratio, 3),
                        "blockSize": len(block),
                        "detail": (
                            "inline <style> block is byte-similar to a ref CSS "
                            "bundle — likely a paste of compiled ref CSS"
                        ),
                    })
                    break


DANGEROUS_RE = re.compile(
    r"dangerouslySetInnerHTML\s*=\s*\{[^}]*?__html\s*:\s*"
    r"(?P<src>[`\"'][\s\S]{200,}?[`\"']|[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
COMPONENT_SCAN = []
for sub in ("src", "app", "pages"):
    sd = impl_root / sub
    if not sd.is_dir():
        continue
    for p in sd.rglob("*"):
        if not p.is_file() or p.suffix not in {".tsx", ".jsx", ".ts", ".js"}:
            continue
        if any(part in {"node_modules", ".next", "dist", "build"} for part in p.parts):
            continue
        COMPONENT_SCAN.append(p)

for path in COMPONENT_SCAN:
    text = read_safe(path)
    if "dangerouslySetInnerHTML" not in text:
        continue
    rel = path.relative_to(impl_root)
    for m in DANGEROUS_RE.finditer(text):
        src = m.group("src")
        # Direct literal — measure paste size; ≥ 1500 chars of inline
        # HTML inside dangerouslySetInnerHTML is overwhelmingly the
        # cheat pattern.
        if src and src[0] in ("`", "\"", "'"):
            body = src[1:-1]
            if len(body) >= 1500 and "<" in body:
                violations.append({
                    "kind": "dangerously-set-innerhtml-large-literal",
                    "file": str(rel),
                    "literalBytes": len(body),
                    "detail": (
                        "dangerouslySetInnerHTML with >=1500-char "
                        "literal — likely a paste of ref body"
                    ),
                })
        else:
            # Variable reference; flag it for review (advisory, lower
            # confidence). Pair-up with the html-paste threshold
            # because the variable could resolve to a small icon.
            violations.append({
                "kind": "dangerously-set-innerhtml-variable",
                "file": str(rel),
                "var": src,
                "detail": (
                    "dangerouslySetInnerHTML with a variable __html — "
                    "review whether the variable resolves to a large "
                    "HTML literal"
                ),
            })


status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "candidatesScanned": [str(p.relative_to(impl_root)) for p in candidates],
    "refTagCount": len(ref_tag_seq),
    "refBundleFilenameCount": len(bundle_filenames),
    "refCssBundleCount": len(ref_css_bodies),
    "violations": violations[:30],
    "rule": (
        "Impl entry HTML/page must not (a) mirror ref's dom-scaffold "
        "(>=70% tag-multiset similarity), (b) load any script whose "
        "filename matches a ref bundle, or (c) inline <style> blocks "
        "byte-similar to ref CSS bundles. All three are gate-game "
        "patterns where the agent pastes ref content instead of "
        "building components."
    ),
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"html-paste: {len(violations)} violation(s) "
    f"({len(candidates)} candidate file(s) scanned) → {status} → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
