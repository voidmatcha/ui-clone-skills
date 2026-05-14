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
#     dimensionMismatches: [...] }
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
  printf '%s\n' '{"schemaVersion":1,"status":"pass","note":"no visible-images.json — nothing to check","total":0,"matched":0,"unmatched":[],"dimensionMismatches":[]}' > "$OUT"
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
from urllib.parse import urlparse

ref_dir = Path(os.environ["REF_DIR"])
impl_dir = Path(os.environ["IMPL_DIR"])
tol_pct = int(os.environ["DIM_TOLERANCE"])
out_path = Path(os.environ["OUT"])
visible_path = ref_dir / "visible-images.json"

try:
    raw = json.loads(visible_path.read_text())
except json.JSONDecodeError as e:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "note": f"visible-images.json malformed: {e}",
        "total": 0, "matched": 0, "unmatched": [], "dimensionMismatches": [],
    }))
    print(f"❌ visible-images.json malformed: {e}", file=sys.stderr)
    sys.exit(2)

# Two shapes seen in the wild: flat array of {src,...} entries OR a wrapper
# array containing {note, images: [...]} when the live site has no images.
if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "images" in raw[0] and "src" not in raw[0]:
    entries = raw[0].get("images", [])
elif isinstance(raw, list):
    entries = raw
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
        "total": 0, "matched": 0, "unmatched": [], "dimensionMismatches": [],
    }))
    print(f"Wrote {out_path} (no image entries — pass)")
    sys.exit(0)

# Index impl source files once. Only text-like extensions to avoid binary scan.
TEXT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".htm",
             ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".astro",
             ".json", ".md", ".mdx", ".yaml", ".yml"}
impl_files = []
for p in impl_dir.rglob("*"):
    if p.is_file() and p.suffix.lower() in TEXT_EXTS:
        try:
            impl_files.append((p, p.read_text(errors="replace")))
        except (OSError, UnicodeDecodeError):
            continue

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

NUM = r"(?:\d+)"
# width/height as JSX prop ({N}), HTML attr ("N" or N), or inline style.
DIM_RE = re.compile(
    rf'(?:width|height)\s*[:=]\s*[{{"\']?\s*({NUM})\s*[}}"\']?',
    re.IGNORECASE,
)

def extract_nearby_dims(content: str, needle: str, window: int = 5):
    """Find the line containing needle, return any width/height numbers within
    ±window lines (a single dict mapping 'width'/'height' → int)."""
    lines = content.splitlines()
    dims = {}
    for i, line in enumerate(lines):
        if needle not in line:
            continue
        lo = max(0, i - window)
        hi = min(len(lines), i + window + 1)
        snippet = "\n".join(lines[lo:hi])
        for m in re.finditer(r'(width|height)\s*[:=]\s*[{"\']?\s*(\d+)', snippet, re.IGNORECASE):
            k = m.group(1).lower()
            if k not in dims:
                dims[k] = int(m.group(2))
        if dims:
            return dims
    return dims

def within_tol(ref: int, impl: int) -> bool:
    if ref <= 0:
        return True
    diff_pct = abs(ref - impl) / ref * 100
    return diff_pct <= tol_pct

unmatched = []
dim_mismatches = []
matched = 0

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

if unmatched:
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
    "tolerance": tol_pct,
}, indent=2))

if status == "pass":
    print(f"✅ image-fidelity: {matched}/{total} matched")
    sys.exit(0)
elif status == "warn":
    print(f"⚠️  image-fidelity: {matched}/{total} matched, {len(dim_mismatches)} dimension mismatch(es)")
    sys.exit(0)
else:
    print(f"❌ image-fidelity: {len(unmatched)}/{total} unmatched")
    for u in unmatched[:5]:
        print(f"   - {u['src']} ({u.get('element','')})")
    if len(unmatched) > 5:
        print(f"   ... and {len(unmatched)-5} more")
    sys.exit(1)
PY
