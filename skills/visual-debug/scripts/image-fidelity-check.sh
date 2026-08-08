#!/usr/bin/env bash
# image-fidelity-check.sh — Static check that every image rendered on the live
# ref page is referenced in the impl source, and that any declared dimensions
# in the impl are within tolerance of the ref's display dimensions.
#
# Why this exists:
#   AE / SSIM compares pixels; when the impl drops a hero image entirely the
#   diff is loud but the *cause* is buried. Catching the dropped/swapped asset
#   at the spec→generation seam — before a full browser sweep — gives a clean
#   "this URL is in ref but not in impl" signal an agent can act on. Same
#   pattern as transition-spec-coverage (presence check, cheap) vs section-
#   compare (pixel compare, expensive).
#
#   The dimension check is the natural extension: for impl files that hard-
#   code width/height (Next.js `<Image>` requires them; framer-motion `<img>`
#   often gets explicit `width=` / `height=` props), the value should be within
#   ±DIM_TOLERANCE of what the ref renders at. Mismatch usually means: agent
#   guessed dimensions, ref uses CSS-driven sizing, or the asset was swapped
#   for a different-aspect placeholder.
#
# Scope:
#   Pure static — no browser, no network. Reads visible-images.json (already
#   captured during ref extraction) and greps the impl source. URL matching
#   tries: full URL → URL basename → basename minus query → basename stem.
#   Dimension matching is best-effort: when impl source has `width={N}` or
#   `width="N"` on the same line or within 5 lines of the matched URL/basename,
#   compare to ref display dims (only available for bg-image entries; <img>
#   entries don't carry display dims in visible-images.json so they always
#   pass the dimension check).
#
# Usage:
#   bash image-fidelity-check.sh <ref-dir> <impl-src-dir>
#
# Env:
#   DIM_TOLERANCE=10   — percent tolerance for dimension comparison (default 10)
#
# Output: <ref-dir>/image-fidelity.json
#   { schemaVersion: 1, status: "pass" | "warn" | "fail",
#     total, matched, unmatched: [...],
#     dimensionMismatches: [...], runtimeImageIssues: [...] }
#
# Exit: 0 = all matched (or no images to check), 1 = unmatched > 0,
#       2 = setup error.

set -uo pipefail

REF_DIR="${1:?Usage: image-fidelity-check.sh <ref-dir> <impl-src-dir>}"
IMPL_DIR="${2:?Missing impl-src-dir}"
DIM_TOLERANCE="${DIM_TOLERANCE:-10}"

if ! [[ "$DIM_TOLERANCE" =~ ^[0-9]+$ ]]; then
  echo "ERROR: DIM_TOLERANCE must be a non-negative integer (got '$DIM_TOLERANCE')" >&2
  exit 2
fi

VISIBLE="$REF_DIR/visible-images.json"
OUT="$REF_DIR/image-fidelity.json"

if [ ! -f "$VISIBLE" ]; then
  python3 - "$OUT" "$IMPL_DIR" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
if impl_dir.name in {"src", "app", "pages"} and (impl_dir.parent / "package.json").exists():
    impl_root = impl_dir.parent
    impl_src = impl_dir
else:
    impl_root = impl_dir
    impl_src = impl_dir / "src" if (impl_dir / "src").is_dir() else impl_dir
out.write_text(json.dumps({
    "schemaVersion": 1,
    "status": "pass",
    "note": "no visible-images.json — nothing to check",
    "total": 0,
    "matched": 0,
    "unmatched": [],
    "dimensionMismatches": [],
    "implRoot": str(impl_root),
    "implDir": str(impl_dir),
    "implSrcDir": str(impl_src),
    "implPublicDir": str(impl_root / "public"),
    "implPkgJson": str(impl_root / "package.json"),
}, indent=2) + "\n")
PY
  echo "Wrote $OUT (no visible-images.json — skipped)"
  exit 0
fi

if [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl source dir not found at $IMPL_DIR" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 required" >&2
  exit 2
fi

# Defer the matching loop to python3 — bash basename/regex juggling for URL
# query stripping + tolerance arithmetic is the kind of code that quietly
# wrong-results when corner-cased. Python keeps the matching predicate auditable.
REF_DIR="$REF_DIR" IMPL_DIR="$IMPL_DIR" DIM_TOLERANCE="$DIM_TOLERANCE" OUT="$OUT" python3 <<'PY'
import json, os, re, sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ref_dir = Path(os.environ["REF_DIR"])
impl_dir = Path(os.environ["IMPL_DIR"])
tol_pct = int(os.environ["DIM_TOLERANCE"])
out_path = Path(os.environ["OUT"])
visible_path = ref_dir / "visible-images.json"


def path_metadata() -> dict:
    """Emit active impl paths so gate.py can reject cross-loop artifacts."""
    if impl_dir.name in {"src", "app", "pages"} and (impl_dir.parent / "package.json").exists():
        impl_root = impl_dir.parent
        impl_src = impl_dir
    else:
        impl_root = impl_dir
        impl_src = impl_dir / "src" if (impl_dir / "src").is_dir() else impl_dir
    return {
        "implRoot": str(impl_root),
        "implDir": str(impl_dir),
        "implSrcDir": str(impl_src),
        "implPublicDir": str(impl_root / "public"),
        "implPkgJson": str(impl_root / "package.json"),
    }

try:
    raw = json.loads(visible_path.read_text())
except json.JSONDecodeError as e:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "note": f"visible-images.json malformed: {e}",
        "total": 0, "matched": 0, "unmatched": [], "dimensionMismatches": [], "runtimeImageIssues": [],
        **path_metadata(),
    }))
    print(f"❌ visible-images.json malformed: {e}", file=sys.stderr)
    sys.exit(2)

# Two shapes seen in the wild: flat array of {src,...} entries OR a wrapper
# array containing {note, images: [...]} when the live site has no images.
if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "images" in raw[0] and "src" not in raw[0]:
    entries = raw[0].get("images", [])
elif isinstance(raw, list):
    entries = raw
elif isinstance(raw, dict):
    for key in ("images", "visible", "entries", "items"):
        if isinstance(raw.get(key), list):
            entries = raw[key]
            break
    else:
        entries = []
else:
    entries = []

# Filter: only entries that actually represent an image (skip notes / SVG-only
# placeholders the extractor sometimes writes).
entries = [e for e in entries if isinstance(e, dict) and e.get("src")]

if not entries:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "note": "no image entries in visible-images.json",
        "total": 0, "matched": 0, "unmatched": [], "dimensionMismatches": [], "runtimeImageIssues": [],
        **path_metadata(),
    }))
    print(f"Wrote {out_path} (no image entries — pass)")
    sys.exit(0)

# Index impl source files once. Only text-like extensions to avoid binary scan.
TEXT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".htm",
             ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".astro",
             ".json", ".md", ".mdx", ".yaml", ".yml"}
impl_files = []
source_manifest_issues = []
visible_urls = [e.get("src", "") for e in entries if isinstance(e.get("src", ""), str)]
visible_needles = set()
for url in visible_urls:
    # Both encoded and decoded forms — keeps needle derivation consistent
    # with asset-placement-check for percent-encoded asset names.
    for candidate in {url, unquote(url)}:
        parsed = urlparse(candidate)
        base = os.path.basename(parsed.path)
        if base:
            visible_needles.add(base)
            stem, _ = os.path.splitext(base)
            if len(stem) >= 4:
                visible_needles.add(stem)
        if candidate:
            visible_needles.add(candidate)
for p in impl_dir.rglob("*"):
    if p.is_file() and p.suffix.lower() in TEXT_EXTS:
        try:
            content = p.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        marker = f"{p.name}\n{content}".lower()
        if "reference-manifest" in marker or "asset-manifest" in marker:
            matched_needles = sorted(n for n in visible_needles if n and n in content)
            if matched_needles:
                source_manifest_issues.append({
                    "implFile": str(p.relative_to(impl_dir)),
                    "matched": matched_needles[:20],
                    "reason": "reference-manifest/asset-manifest strings are not rendered image usage",
                })
            continue
        impl_files.append((p, content))

def url_keys(url: str):
    """Yield progressively-broader needles for a URL: full URL, basename with
    query, basename without query, stem (no extension)."""
    yield url
    parsed = urlparse(url)
    base = os.path.basename(parsed.path)
    if base:
        yield base
        # strip query — already done by urlparse for path, but cover the case
        # where the agent stored "src" with query glued on.
        if "?" in url:
            yield url.split("?", 1)[0].rsplit("/", 1)[-1]
        stem, ext = os.path.splitext(base)
        if stem and len(stem) >= 4:
            yield stem

def find_match(url: str):
    for needle in url_keys(url):
        if not needle:
            continue
        for path, content in impl_files:
            if needle in content:
                return path, needle
    return None, None

# width/height as JSX prop ({N}), HTML attr ("N" or N), or inline style. The
# leading (?<![A-Za-z-]) is load-bearing: without it the case-insensitive `width`
# also matches inside `maxWidth`/`minWidth`/`min-width` (and `height` inside
# `minHeight`/`max-height`), so the reader would pick an ANCESTOR container's
# maxWidth/minHeight instead of the element's own width/height (the eBay
# grid-tile false 1344×220 read). The boundary anchors to the real property name.
DIM_RE = re.compile(
    r'(?<![A-Za-z-])(width|height)\s*[:=]\s*[{"\']?\s*(\d+)',
    re.IGNORECASE,
)

def _dims_in(text: str) -> dict:
    dims: dict = {}
    for m in DIM_RE.finditer(text):
        k = m.group(1).lower()
        if k not in dims:
            dims[k] = int(m.group(2))
    return dims

def extract_nearby_dims(content: str, needle: str, window: int = 5):
    """Return the element's own width/height numbers (a dict mapping
    'width'/'height' → int). Prefer dims on the needle line ITSELF — that is the
    element carrying the matched URL, so its own `width`/`height` win over an
    ancestor container's within ±window lines. Only widen to the ±window when the
    needle line carries no dimension of its own."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if needle not in line:
            continue
        own = _dims_in(line)
        if own:
            return own
        lo = max(0, i - window)
        hi = min(len(lines), i + window + 1)
        widened = _dims_in("\n".join(lines[lo:hi]))
        if widened:
            return widened
    return {}

def within_tol(ref: int, impl: int) -> bool:
    if ref <= 0:
        return True
    diff_pct = abs(ref - impl) / ref * 100
    return diff_pct <= tol_pct

unmatched = []
dim_mismatches = []
runtime_image_issues = []
matched = 0

visible_basenames = {
    os.path.basename(urlparse(e.get("src", "")).path)
    for e in entries
    if e.get("src", "").startswith(("http://", "https://"))
}
visible_basenames = {b for b in visible_basenames if b}

# Local clones must reference transferred files under /images, /video, /font,
# etc. A relative /cdn-cgi/image/... URL asks the local dev server to emulate
# Cloudflare's optimizer and 404s in browsers even when public/images/foo.webp
LOCAL_CDN_RE = re.compile(r'(?P<quote>["\'])?(?P<path>/cdn-cgi/image/[^"\'\s)>]+)')
seen_runtime_issue_keys: set[tuple[str, str]] = set()
for path, content in impl_files:
    for match in LOCAL_CDN_RE.finditer(content):
        cdn_path = match.group("path").rstrip("\\")
        matched_base = next((base for base in visible_basenames if base in cdn_path), "")
        if visible_basenames and not matched_base:
            continue
        issue_key = (str(path.relative_to(impl_dir)), matched_base or cdn_path)
        if issue_key in seen_runtime_issue_keys:
            continue
        seen_runtime_issue_keys.add(issue_key)
        start = max(0, match.start("path") - 80)
        end = min(len(content), match.end("path") + 80)
        runtime_image_issues.append({
            "kind": "local-cdn-optimizer-path",
            "implFile": str(path.relative_to(impl_dir)),
            "path": cdn_path,
            "snippet": content[start:end].replace("\n", "\\n"),
            "reason": "Local impls do not serve Cloudflare /cdn-cgi/image optimizer URLs; rewrite to the transferred public asset path.",
        })

for entry in entries:
    src = entry.get("src", "")
    if not src or not src.startswith(("http://", "https://")):
        # Skip data URIs / inline svg refs / blank entries. Those aren't a
        # fidelity concern — visible-images.json's collector already filters
        # to https://, but bg-image collector accepts http:// too.
        continue
    path, needle = find_match(src)
    if not path:
        unmatched.append({"src": src, "element": entry.get("element", ""), "type": entry.get("type", "")})
        continue
    matched += 1
    # Dimension check only for bg-image entries (carry width/height in the
    # extraction). <img> entries don't have ref-side display dims so we can't
    # compare; presence-match is the strongest signal we can give them.
    ref_w = entry.get("width")
    ref_h = entry.get("height")
    if not (isinstance(ref_w, int) and isinstance(ref_h, int)):
        continue
    impl_content = next((c for p, c in impl_files if p == path), "")
    impl_dims = extract_nearby_dims(impl_content, needle)
    if not impl_dims:
        # No explicit dims in impl — CSS may size it; can't audit further.
        continue
    bad = []
    if "width" in impl_dims and not within_tol(ref_w, impl_dims["width"]):
        bad.append(f"width: ref={ref_w} impl={impl_dims['width']}")
    if "height" in impl_dims and not within_tol(ref_h, impl_dims["height"]):
        bad.append(f"height: ref={ref_h} impl={impl_dims['height']}")
    if bad:
        dim_mismatches.append({
            "src": src,
            "implFile": str(path.relative_to(impl_dir)),
            "refDims": {"width": ref_w, "height": ref_h},
            "implDims": impl_dims,
            "issues": bad,
        })

total = sum(
    1 for e in entries
    if e.get("src", "").startswith(("http://", "https://"))
)

if unmatched or runtime_image_issues:
    status = "fail"
elif source_manifest_issues:
    status = "fail"
elif dim_mismatches:
    status = "warn"
else:
    status = "pass"

out_path.write_text(json.dumps({
    "schemaVersion": 1,
    "status": status,
    "total": total,
    "matched": matched,
    "unmatched": unmatched,
    "dimensionMismatches": dim_mismatches,
    "runtimeImageIssues": runtime_image_issues,
    "sourceManifestIssues": source_manifest_issues,
    "tolerance": tol_pct,
    **path_metadata(),
}, indent=2))

if status == "pass":
    print(f"✅ image-fidelity: {matched}/{total} matched")
    sys.exit(0)
elif status == "warn":
    print(f"⚠️  image-fidelity: {matched}/{total} matched, {len(dim_mismatches)} dimension mismatch(es)")
    sys.exit(0)
else:
    print(f"❌ image-fidelity: {len(unmatched)}/{total} unmatched, {len(runtime_image_issues)} runtime image issue(s), {len(source_manifest_issues)} source manifest issue(s)")
    for u in unmatched[:5]:
        print(f"   - {u['src']} ({u.get('element','')})")
    if len(unmatched) > 5:
        print(f"   ... and {len(unmatched)-5} more")
    for issue in runtime_image_issues[:5]:
        print(f"   - {issue['kind']}: {issue['implFile']} uses {issue['path']}")
    if len(runtime_image_issues) > 5:
        print(f"   ... and {len(runtime_image_issues)-5} more runtime image issue(s)")
    for issue in source_manifest_issues[:5]:
        print(f"   - source-manifest: {issue['implFile']}")
    if len(source_manifest_issues) > 5:
        print(f"   ... and {len(source_manifest_issues)-5} more source manifest issue(s)")
    sys.exit(1)
PY
